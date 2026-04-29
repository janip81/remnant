@AGENTS.md
@MVP.md

## Claude Session Rules

- **Start every session**: read AGENTS.md → MVP.md → PLAN.md, then state which phase you are on and what the next unchecked task is
- **End every session**: write a memory file to `memory/YYYY-MM-DD_topic_phase.md` — always, even for short sessions
- **Check ISSUES.md** before starting work — don't duplicate known issues
- **Never commit to main unless building MVP** — always branch + PR after MVP finished
- **Never push unless building MVP** without the user asking unless in mvp stage
- **During initial MVP where a lot of builds are to be made, build+push locally not with github actions** After coding settles down we use github actions
- **Never add Co-Authored-By** lines to commits
- **Keep PLAN.md updated** — check off tasks as they are completed
