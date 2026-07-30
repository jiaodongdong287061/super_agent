# Gitlab Skill

An AI agent skill for GitLab integration — fetch issues and merge requests, generate executive summaries, perform structured code reviews, and automate issue resolution workflows.

Built as a [Claude Code / Cursor skill](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/skills), it works with any AI coding assistant that supports skill-based orchestration.

## Quick Install

```bash
curl -fsSL https://raw.githubusercontent.com/ekohe/gitlab-skill/main/install.sh | bash
```

This clones the repo into your skills directory, installs dependencies, and creates a config file for you to fill in. Re-run to update.

**Manual install:**

```bash
git clone https://github.com/ekohe/gitlab-skill.git ~/.claude/skills/gitlab-skill
cd ~/.claude/skills/gitlab-skill
pip install -r requirements.txt
cp gitlab_config.json.template gitlab_config.json
```

## Features

- **Multi-instance** — manage multiple GitLab servers from one config
- **Project aliases** — short names with automatic instance routing
- **Project management** — create projects with default branches (dev/test/prod), fork projects, delete projects
- **Issue summaries** — executive-level summaries focused on business impact
- **Code reviews** — structured analysis across Security, Logic, Performance, and Quality
- **Issue resolution** — end-to-end: fetch issue, branch, implement, MR, comment
- **Project insights** — aggregate statistics and trend analysis

## Creating a GitLab Personal Access Token

A Personal Access Token (PAT) is required to authenticate with the GitLab API.

### Steps

1. Sign in to your GitLab instance
2. Go to **User Settings > Access Tokens**
   - GitLab.com: https://gitlab.com/-/user_settings/personal_access_tokens
   - Self-hosted: `https://<your-gitlab>/–/user_settings/personal_access_tokens`
3. Click **Add new token**
4. Fill in the fields:
   - **Token name:** e.g. `Gitlab Skill`
   - **Expiration date:** set a reasonable expiry (max 1 year recommended)
   - **Scopes:** select **`api`** (full API access — required for reading issues/MRs and posting comments)
5. Click **Create personal access token**
6. Copy the token immediately (it won't be shown again)

### Scope Reference

| Scope | Required | Why |
|-------|----------|-----|
| `api` | Yes | Read/write access to issues, MRs, comments, and diffs |
| `read_api` | Partial | Read-only — sufficient if you don't need to post comments or create MRs |

### Security Best Practices

- Never commit tokens to version control (`gitlab_config.json` is in `.gitignore`)
- Set file permissions: `chmod 600 gitlab_config.json`
- Use short expiration dates and rotate regularly
- Create separate tokens per tool/integration
- Revoke tokens immediately if compromised

## Configuration

Edit `gitlab_config.json` (created by the installer from the template):

```json
{
  "default": "work",
  "instances": {
    "work": {
      "url": "https://gitlab.company.com",
      "token": "glpat-xxxxxxxxxxxxxxxxxxxx",
      "description": "Company GitLab"
    }
  },
  "projects": {
    "webapp": {
      "project_id": "acme/webapp",
      "instance": "work",
      "description": "Main web app"
    }
  }
}
```

You can also use environment variables for a single instance:

```bash
export GITLAB_URL="https://gitlab.com"
export GITLAB_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx"
```

Verify your setup:

```bash
python scripts/gitlab_api.py list-instances
python scripts/gitlab_api.py list-projects
```

## How It Works

The skill provides two CLI scripts that your AI agent orchestrates automatically:

| Script | Purpose |
|--------|---------|
| `scripts/gitlab_api.py` | Core API client — fetch issues, MRs, diffs, post comments, aggregate stats |
| `scripts/auto_resolve_issue.py` | Resolution workflow — create branches, push, create MRs |

When used as an AI skill, the agent reads `SKILL.md` for detailed workflow instructions covering:

- **Create Project** — create a new project with dev/test/prod branches automatically
- **Fork Project** — fork an existing project to your namespace
- **Summarize Issue** — fetch + executive summary using `references/issue_summary_format.md`
- **Review MR** — fetch + structured code review using `references/code_review_style.md`
- **Resolve Issue** — full loop from issue to merge request
- **Aggregate Insights** — project health reports
- **List & Filter** — issues/MRs by state and labels

See `SKILL.md` for the complete CLI reference and workflow details.

## Project Structure

```
gitlab-skill/
├── SKILL.md                        # Agent instructions & full CLI reference
├── install.sh                      # One-command installer
├── gitlab_config.json.template     # Config template (safe to share)
├── requirements.txt                # Python dependencies
├── scripts/
│   ├── gitlab_api.py               # GitLab API client & CLI
│   └── auto_resolve_issue.py       # Issue resolution automation
└── references/
    ├── issue_summary_format.md     # Executive summary format guide
    └── code_review_style.md        # Code review style guide
```

## Contributing

Contributions are welcome! Fork the repo, create a feature branch, and open a Pull Request.

## License

[MIT License](LICENSE)

## Contributors

- [Encore](https://github.com/encoreshao) — creator & maintainer

---

Made by [Ekohe](https://ekohe.com) — [github.com/ekohe/gitlab-skill](https://github.com/ekohe/gitlab-skill)
