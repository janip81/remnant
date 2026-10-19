"""Synthetic lifecycle events; no real memory writes or credential reads."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import io
from unittest.mock import Mock
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("remnant_hooks", ROOT / "remnant-hooks.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-hooks-", dir=ROOT)
        self.home = Path(self.temp.name)
        (self.home / "sessions").mkdir()
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(self.home), "AGENT_DELEGATION_DEPTH": "0",
                              "REMNANT_INJECT_POLICY": "selective", "REMNANT_BENCH_LOG": "",
                              "REMNANT_BENCH_READ_ONLY": "0"})
        self.env.start()
        self.transcript = self.home / "sessions/transcript.jsonl"
        records = [
            {"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Fix the memory transport; REMNANT_TOKEN=synthetic-secret"}]}},
            {"type":"response_item","payload":{"type":"message","role":"assistant","phase":"commentary","content":[{"type":"output_text","text":"PRIVATE_ANALYSIS_SENTINEL"}]}},
            {"type":"response_item","payload":{"type":"function_call_output","output":"TOOL_SECRET_SENTINEL"}},
            {"type":"response_item","payload":{"type":"message","role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"Verified the transport with read-only calls and documented the configuration. "*3}]}},
        ]
        self.transcript.write_text("\n".join(json.dumps(r) for r in records)+"\n")

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def event(self, name):
        return {"hook_event_name":name,"session_id":"synthetic-session",
                "cwd":"/workspace/project","transcript_path":str(self.transcript)}

    def test_search_context_bounded_and_redacted(self):
        event={**self.event("UserPromptSubmit"),"prompt":"Review node-a status; password=synthetic-secret"}
        response=self.hits([self.row("node-a status\ntoken=RESULT_SECRET")])
        with patch.object(hook,"rpc",return_value=response) as rpc:
            result=hook.handle(event)
        self.assertNotIn("synthetic-secret",rpc.call_args.args[1]["query"])
        context=result["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("RESULT_SECRET",context)
        self.assertLessEqual(len(context),hook.PROMPT_LIMIT)
        self.assertNotIn("Remnant: before finishing",context)
        self.assertEqual(rpc.call_args.args[1]["limit"],10)
        self.assertEqual(rpc.call_args.args[1]["format"],"json")
        self.assertFalse(rpc.call_args.args[1]["query"].startswith("project"))

    def test_delegation_and_stop_loop_skip(self):
        with patch.dict(os.environ,{"AGENT_DELEGATION_DEPTH":"1"}),patch.object(hook,"rpc") as rpc:
            self.assertEqual(hook.handle(self.event("SessionStart")),{})
            rpc.assert_not_called()
        with patch.object(hook,"rpc") as rpc:
            self.assertEqual(hook.handle({**self.event("Stop"),"stop_hook_active":True}),{})
            rpc.assert_not_called()

    def test_snapshot_identity_filtering_and_dedup(self):
        with patch.object(hook,"rpc",return_value={"content":[{"type":"text","text":'{"saved": true}'}]}) as rpc:
            result=hook.handle(self.event("PreCompact"))
            self.assertIn("saved",result["systemMessage"])
            args=rpc.call_args.args[1]
            self.assertEqual(args["agent_id"],"codex")
            self.assertEqual(rpc.call_args.args[0],"save_checkpoint")
            self.assertEqual(args["session_id"],"synthetic-session")
            self.assertTrue(args["sync"])
            for sentinel in ("synthetic-secret","PRIVATE_ANALYSIS_SENTINEL","TOOL_SECRET_SENTINEL"):
                self.assertNotIn(sentinel,args["content"])
            self.assertEqual(hook.handle(self.event("Stop")),{})
            self.assertEqual(rpc.call_count,1)
        state=next((self.home/"remnant-hook-state").glob('*.json')).read_text()
        self.assertEqual(list(json.loads(state)),["snapshot", "captured_at"])
        self.assertNotIn("Verified",state)

    def test_subprocess_http_checkpoint_lifecycle(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        calls=[]
        saved={}
        hits=self.hits([self.row('node-a synthetic configuration')])
        class Server(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                msg=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                tool=msg['params']['name'];args=msg['params']['arguments'];calls.append(tool)
                if tool=='search_memory':result=hits
                elif tool=='save_checkpoint':
                    saved[args['session_id']]=args['content']
                    result={'content':[{'type':'text','text':json.dumps({'saved':True})}]}
                elif tool=='get_checkpoint':
                    result={'content':[{'type':'text','text':saved[args['session_id']]}]}
                else:raise AssertionError(tool)
                body=json.dumps({'jsonrpc':'2.0','id':1,'result':result}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Server)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        (self.home/'config.toml').write_text('[mcp_servers.remnant]\nurl="http://127.0.0.1:'+str(server.server_port)+'"\n')
        env={**os.environ,'REMNANT_TOKEN':'synthetic-test-only','REMNANT_URL':''}
        def run(event,depth='0'):
            result=subprocess.run([sys.executable,'-B',str(ROOT/'remnant-hooks.py')],
                input=json.dumps(event),text=True,capture_output=True,
                env={**env,'AGENT_DELEGATION_DEPTH':depth},timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            return json.loads(result.stdout)
        try:
            self.assertIn('node-a',str(run(self.prompt())))
            before=len(calls);self.assertEqual(run(self.prompt(),depth='1'),{});self.assertEqual(len(calls),before)
            self.assertIn('saved',str(run(self.event('PreCompact'))))
            for source in ('resume','compact'):
                self.assertIn('Verified the transport',str(run({**self.event('SessionStart'),'source':source})))
            self.assertEqual(calls,['search_memory','save_checkpoint','get_checkpoint','get_checkpoint'])
            self.assertNotIn('synthetic-secret',str(saved))
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

    def test_failure_is_advisory(self):
        with patch.object(hook,"rpc",side_effect=OSError("SECRET_SENTINEL")):
            output=hook.handle(self.event("SessionStart"))
            self.assertNotIn("SECRET_SENTINEL",json.dumps(output))
            output=hook.handle(self.event("PreCompact"))
            self.assertNotIn("SECRET_SENTINEL",json.dumps(output))
            self.assertNotIn("decision",output)

    def test_trivial_commands_and_restricted_modes(self):
        with patch.object(hook,"rpc") as rpc:
            self.assertEqual(hook.handle({**self.event("UserPromptSubmit"),"prompt":"ok"}),{})
            self.assertEqual(hook.handle({**self.event("UserPromptSubmit"),"prompt":"/clear"}),{})
            self.assertEqual(hook.handle({**self.event("PreCompact"),"permission_mode":"plan"}),{})
            self.assertEqual(hook.handle({**self.event("Stop"),"last_assistant_message":"OK"}),{})
            rpc.assert_not_called()

    def test_explicit_no_memory_write(self):
        self.transcript.write_text(json.dumps({"type":"response_item","payload":{
            "type":"message","role":"user","content":[{"type":"input_text","text":"Do not modify any memories."}]}}))
        event={**self.event("Stop"),"last_assistant_message":"Read-only response. "*20}
        with patch.object(hook,"rpc") as rpc:
            self.assertEqual(hook.handle(event),{})
            rpc.assert_not_called()

    def test_restore_exact_session_without_broad_search(self):
        for source in ("resume", "compact"):
            with patch.object(hook,"rpc",side_effect=[
                {"content":[{"type":"text","text":"SESSION_CHECKPOINT"}]},
                {"content":[{"type":"text","text":"DURABLE_FACT"}]}]) as rpc:
                result=hook.handle({**self.event("SessionStart"),"source":source})
            self.assertEqual(rpc.call_args_list[0].args,("get_checkpoint",{"session_id":"synthetic-session"}))
            self.assertEqual(rpc.call_count,1)
            self.assertIn("SESSION_CHECKPOINT",str(result))
            self.assertNotIn("DURABLE_FACT",str(result))

    @staticmethod
    def hits(rows):
        return {"content":[{"type":"text","text":json.dumps(rows)}]}

    @staticmethod
    def row(text, mid="memory-one", state="active"):
        return {"id":mid,"memory":text,"state":state}

    def prompt(self, text="Please check node-a health and configuration."):
        return {**self.event("UserPromptSubmit"),"prompt":text}

    def test_identifier_boundaries_unicode_paths_ips_and_versions(self):
        ids=hook.identifiers("Check åäö-config /opt/git/project 192.168.1.5 1.35 v1.2.3 12345 node-a")
        self.assertEqual(ids,["åäö-config","/opt/git/project","192.168.1.5","node-a"])
        for text in ("node-ab", "node-a.example.org", "/node-a-old/path", "xnode-a"):
            self.assertFalse(hook.mentions(text,"node-a"))
        self.assertTrue(hook.mentions("Check NODE-A.","node-a"))
        # Path components count: '/' is a boundary on both sides.
        self.assertTrue(hook.mentions("/srv/node-a/path","node-a"))
        self.assertTrue(hook.mentions("changed agent-tools/delegate.py","delegate.py"))
        self.assertFalse(hook.mentions("/etc/hosts.allow","/etc/hosts"))
        with patch.object(hook,"rpc",return_value=self.hits([
                self.row("node-ab unrelated", "wrong"), self.row("NODE-A correct", "right")])):
            result=hook.handle(self.prompt())
        self.assertIn("NODE-A correct",str(result))
        self.assertNotIn("unrelated",str(result))

    def test_generic_prompts_and_redacted_identifiers_do_not_search(self):
        for text in ("How do I improve memory quality overall?", "Please review version 1.35 and 12345.",
                     "password=secret-node-a", "OK", "/clear"):
            with patch.object(hook,"rpc") as rpc:
                self.assertEqual(hook.handle(self.prompt(text)),{})
                rpc.assert_not_called()

    def test_repeated_memory_compaction_reset_and_snapshot_state_separate(self):
        response=self.hits([self.row("node-a useful fact")])
        with patch.object(hook,"rpc",return_value=response):
            self.assertTrue(hook.handle(self.prompt()))
            self.assertEqual(hook.handle(self.prompt()),{})
        with patch.object(hook,"rpc",return_value={"content":[{"type":"text","text":'{"saved":true}'}]}):
            hook.handle(self.event("Stop"))
        with patch.object(hook,"rpc",return_value=response):
            self.assertEqual(hook.handle(self.prompt()),{})
            hook.handle({**self.event("SessionStart"),"source":"compact"})
            self.assertTrue(hook.handle(self.prompt()))

    def test_states_candidates_history_and_sessions_excluded(self):
        rows=[self.row("node-a forbidden",str(i),state) for i,state in enumerate(
              ("candidate","superseded","archived","rejected"))]
        rows += [{**self.row("node-a historical session","s"),"category":"session"},
                 {"id":"missing-state","memory":"node-a missing state"},
                 self.row("node-a active fact","good")]
        with patch.object(hook,"rpc",return_value=self.hits(rows)):
            result=hook.handle(self.prompt())
        self.assertIn("active fact",str(result))
        for forbidden in ("forbidden","historical session","missing state"):
            self.assertNotIn(forbidden,str(result))

    def test_full_render_budget_and_whole_memories(self):
        rows=[self.row("node-a "+"x"*3100,"oversize")]
        rows += [self.row("node-a "+str(i)+" "+"x"*900,str(i)) for i in range(5)]
        with patch.object(hook,"rpc",return_value=self.hits(rows)):
            out=hook.handle(self.prompt())["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(out),3000)
        self.assertEqual(out.count("node-a"),3)
        self.assertTrue(out.endswith("</memory>"))

    def test_malformed_oversized_and_unavailable_fail_quietly(self):
        for text in ("[", "{}", '["not a row"]', "x"*(hook.RPC_LIMIT+1)):
            with patch.object(hook,"rpc",return_value={"content":[{"type":"text","text":text}]}):
                self.assertEqual(hook.handle(self.prompt()),{})
        with patch.object(hook,"rpc",side_effect=OSError("SECRET_SENTINEL")):
            self.assertEqual(hook.handle(self.prompt()),{})

    def test_rpc_rejects_truncated_oversized_envelope(self):
        fake=Mock()
        fake.open.return_value=nullcontext(io.BytesIO(b" "*(hook.RPC_LIMIT+1)))
        with patch.object(hook,"credentials",return_value=("https://example.invalid/mcp","synthetic")), \
             patch.object(hook.urllib.request,"build_opener",return_value=fake):
            with self.assertRaises(ValueError):
                hook.rpc("search_memory",{})

    def test_concurrent_prompts_inject_each_id_once(self):
        with patch.object(hook,"rpc",return_value=self.hits([self.row("node-a concurrent fact")])):
            with ThreadPoolExecutor(max_workers=6) as pool:
                results=list(pool.map(lambda _:hook.handle(self.prompt()),range(6)))
        self.assertEqual(sum(bool(r) for r in results),1)

    def test_cache_is_bounded(self):
        with hook.injection_cache(self.prompt()) as (_, path):
            hook.store_ids(path,{f"{i:064x}" for i in range(hook.CACHE_LIMIT)})
        with patch.object(hook,"rpc",return_value=self.hits([self.row("node-a too late")])):
            self.assertEqual(hook.handle(self.prompt()),{})

    def test_corrupt_cache_does_not_block_checkpoint_and_recovers(self):
        path,_=hook.injection_paths(self.prompt())
        path.write_text('{bad JSON')
        response={"content":[{"type":"text","text":'{"saved":true}'}]}
        with patch.object(hook,"rpc",return_value=response) as rpc:
            result=hook.handle(self.event("PreCompact"))
            self.assertIn("saved",result["systemMessage"])
            self.assertEqual(rpc.call_args.args[0],"save_checkpoint")
        with patch.object(hook,"rpc",return_value=self.hits([self.row("node-a recovered")])):
            self.assertTrue(hook.handle(self.prompt()))

    def test_valid_json_with_invalid_cached_ids_is_rebuilt(self):
        path,_=hook.injection_paths(self.prompt())
        path.write_text(json.dumps({"generation":"","ids":["x"]}))
        with patch.object(hook,"rpc",return_value=self.hits([self.row("node-a recovered")])):
            self.assertTrue(hook.handle(self.prompt()))
        self.assertEqual(len(json.loads(path.read_text())["ids"][0]),64)

    def test_compaction_survives_held_lock_and_late_cache_writer(self):
        def answer(tool,args):
            return {"content":[{"type":"text","text":
                '{"saved":true}' if tool=="save_checkpoint" else "SESSION_CHECKPOINT"}]}
        with hook.injection_cache(self.prompt()) as (_, target):
            with patch.object(hook,"rpc",side_effect=answer) as rpc:
                self.assertIn("saved",hook.handle(self.event("PreCompact"))["systemMessage"])
                restored=hook.handle({**self.event("SessionStart"),"source":"compact"})
                self.assertIn("SESSION_CHECKPOINT",str(restored))
                self.assertEqual([c.args[0] for c in rpc.call_args_list],["save_checkpoint","get_checkpoint"])
            hook.store_ids(target,{hook.hashlib.sha256(b"memory-one").hexdigest()})
        with patch.object(hook,"rpc",return_value=self.hits([self.row("node-a restored fact")])):
            self.assertTrue(hook.handle(self.prompt()))

    def test_policies_and_metadata_only_benchmark_events(self):
        log=self.home/"bench.jsonl"
        rows=self.hits([self.row("node-a MEMORY_TEXT_SENTINEL")])
        with patch.dict(os.environ,{"REMNANT_BENCH_LOG":str(log),"REMNANT_BENCH_RUN":"test-run"}):
            for policy in ("off","broad","selective"):
                with patch.dict(os.environ,{"REMNANT_INJECT_POLICY":policy}), \
                     patch.object(hook,"rpc",return_value=rows) as rpc:
                    result=hook.handle(self.prompt("Please inspect node-a PROMPT_SENTINEL"))
                    self.assertEqual(bool(result),policy!="off")
                    if policy=="off":rpc.assert_not_called()
                    if policy=="broad":self.assertTrue(rpc.call_args.args[1]["query"].startswith("project "))
            with patch.dict(os.environ,{"REMNANT_BENCH_READ_ONLY":"1"}),patch.object(hook,"rpc") as rpc:
                self.assertEqual(hook.handle(self.event("Stop")),{})
                rpc.assert_not_called()
        text=log.read_text()
        self.assertNotIn("PROMPT_SENTINEL",text)
        self.assertNotIn("MEMORY_TEXT_SENTINEL",text)
        self.assertNotIn("node-a",text)
        events=[json.loads(line) for line in text.splitlines()]
        self.assertEqual(len(events),4)
        self.assertEqual([e["policy"] for e in events[:3]],["off","broad","selective"])
        self.assertEqual(events[1]["injected_count"],1)
        self.assertEqual(log.stat().st_mode&0o777,0o600)

    def test_throttle_and_precompact_bypass(self):
        response={"content":[{"type":"text","text":'{"saved": true}'}]}
        with patch.object(hook,"rpc",return_value=response) as rpc, patch.object(hook.time,"time",return_value=2000):
            hook.handle(self.event("Stop"))
            self.assertFalse(rpc.call_args.args[1]["sync"])
            changed={**self.event("Stop"),"last_assistant_message":"Changed result. "*20}
            self.assertEqual(hook.handle(changed),{})
            self.assertEqual(rpc.call_count,1)
            hook.handle({**changed,"hook_event_name":"PreCompact"})
            self.assertTrue(rpc.call_args.args[1]["sync"])
            self.assertEqual(rpc.call_count,2)
        with patch.object(hook,"rpc",return_value=response) as rpc, patch.object(hook.time,"time",return_value=3000):
            hook.handle(self.event("Stop"))
            rpc.assert_called_once()

    def test_checkpoint_failure_does_not_throttle_retry(self):
        response={"content":[{"type":"text","text":"checkpoint not saved: OSError"}]}
        with patch.object(hook,"rpc",return_value=response) as rpc:
            hook.handle(self.event("Stop"))
            hook.handle(self.event("Stop"))
            self.assertEqual(rpc.call_count,2)
        self.assertEqual(list((self.home/"remnant-hook-state").glob("*.json")),[])

    def test_installer_preserves_existing_hooks_and_mcp(self):
        original={"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"existing-hook"}]}]}}
        (self.home/"hooks.json").write_text(json.dumps(original))
        (self.home/"config.toml").write_text('[mcp_servers.remnant]\ncommand="unchanged"\n')
        before=(self.home/"config.toml").read_bytes()
        for _ in range(2):
            r=subprocess.run([sys.executable,str(ROOT/"install-remnant-hooks.py")],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
        result=json.loads((self.home/"hooks.json").read_text())
        self.assertEqual(result['hooks']['SessionStart'][0],original['hooks']['SessionStart'][0])
        self.assertEqual(len(result['hooks']['SessionStart']),2)
        self.assertEqual((self.home/"config.toml").read_bytes(),before)
        self.assertEqual(len(list(self.home.glob('hooks.json.backup-*'))),1)


    def install(self, *arguments):
        return subprocess.run([sys.executable,str(ROOT/"install-remnant-hooks.py"),*arguments],
                              capture_output=True,text=True)

    def legacy_document(self):
        return {"otherSetting":{"keep":True},"hooks":{event:[{"hooks":[{
            "type":"command","command":"python3.11 /opt/git/prod-k8s/agent-tools/remnant-hooks.py",
            "timeout":12,"customField":"preserved"}]}]
            for event in ("SessionStart","UserPromptSubmit","PreCompact","Stop")}}

    def test_migrate_replaces_old_preserves_unrelated_and_is_idempotent(self):
        original=self.legacy_document()
        unrelated={"type":"command","command":"unrelated-script","timeout":9}
        original["hooks"]["SessionStart"][0]["hooks"].append(unrelated)
        original["hooks"]["SessionStart"][0]["matcher"]="preserved-matcher"
        original["hooks"]["PostToolUse"]=[{"hooks":[unrelated]}]
        (self.home/"hooks.json").write_text(json.dumps(original))
        (self.home/"config.toml").write_text("unmodified-config-sentinel")
        r=self.install("--migrate");self.assertEqual(r.returncode,0,r.stderr)
        after=json.loads((self.home/"hooks.json").read_text())
        self.assertEqual(after["otherSetting"],original["otherSetting"])
        self.assertEqual(after["hooks"]["PostToolUse"],original["hooks"]["PostToolUse"])
        self.assertEqual(after["hooks"]["SessionStart"][0]["hooks"][1],unrelated)
        self.assertEqual(after["hooks"]["SessionStart"][0]["matcher"],"preserved-matcher")
        for event in ("SessionStart","UserPromptSubmit","PreCompact","Stop"):
            owned=[h for g in after["hooks"][event] for h in g["hooks"]
                   if "remnant-hooks.py" in h.get("command","")]
            self.assertEqual(len(owned),1)
            self.assertIn(str(ROOT/"remnant-hooks.py"),owned[0]["command"])
            self.assertEqual(owned[0]["customField"],"preserved")
        before=(self.home/"hooks.json").read_bytes()
        r=self.install("--migrate");self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual((self.home/"hooks.json").read_bytes(),before)
        self.assertEqual(len(list(self.home.glob("hooks.json.backup-*"))),1)
        self.assertEqual((self.home/"config.toml").read_text(),"unmodified-config-sentinel")

    def test_migrate_deduplicates_mixed_old_and_new_handlers(self):
        doc=self.legacy_document()
        new={"type":"command","command":"python3.11 "+str(ROOT/"remnant-hooks.py")}
        for event in doc["hooks"]:
            doc["hooks"][event].append({"hooks":[new,doc["hooks"][event][0]["hooks"][0]]})
        (self.home/"hooks.json").write_text(json.dumps(doc))
        r=self.install("--migrate");self.assertEqual(r.returncode,0,r.stderr)
        after=json.loads((self.home/"hooks.json").read_text())
        for groups in after["hooks"].values():
            self.assertEqual(sum(len(g["hooks"]) for g in groups),1)

    def test_without_migrate_legacy_is_rejected_without_writing(self):
        before=json.dumps(self.legacy_document())
        (self.home/"hooks.json").write_text(before)
        r=self.install();self.assertNotEqual(r.returncode,0)
        self.assertIn("--migrate",r.stderr)
        self.assertEqual((self.home/"hooks.json").read_text(),before)
        self.assertEqual(list(self.home.glob("hooks.json.backup-*")),[])

    def test_unknown_remnant_installation_is_rejected_without_writing(self):
        original={"hooks":{"Stop":[{"hooks":[{"type":"command",
            "command":"python3.11 /unknown/remnant-hooks.py"}]}]}}
        before=json.dumps(original);(self.home/"hooks.json").write_text(before)
        r=self.install("--migrate");self.assertNotEqual(r.returncode,0)
        self.assertEqual((self.home/"hooks.json").read_text(),before)

    def test_migrate_adds_missing_events_and_quotes_path_with_spaces(self):
        target=self.home/"with spaces";target.mkdir()
        hookpath=target/"remnant-hooks.py";hookpath.write_text("# synthetic target")
        (self.home/"hooks.json").write_text('{"hooks":{}}')
        r=self.install("--migrate","--hook-path",str(hookpath))
        self.assertEqual(r.returncode,0,r.stderr)
        import shlex
        after=json.loads((self.home/"hooks.json").read_text())
        self.assertEqual(set(after["hooks"]),{"SessionStart","UserPromptSubmit","PreCompact","Stop"})
        for groups in after["hooks"].values():
            self.assertEqual(shlex.split(groups[0]["hooks"][0]["command"]),["python3.11",str(hookpath)])

    def test_malformed_document_fails_without_write_or_leaking_contents(self):
        for before in ('{bad-json-private-sentinel', '{"hooks": []}', '[]'):
            (self.home/"hooks.json").write_text(before)
            r=self.install("--migrate");self.assertNotEqual(r.returncode,0)
            self.assertEqual((self.home/"hooks.json").read_text(),before)
            self.assertNotIn("private-sentinel",r.stderr)


if __name__=="__main__":
    unittest.main(verbosity=2)
