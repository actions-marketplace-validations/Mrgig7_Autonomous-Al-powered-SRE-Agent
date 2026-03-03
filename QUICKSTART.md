# 🚀 SRE Agent Quick Start

Get up and running in **5 minutes**.

---

## Option 1: GitHub Action (Easiest)

Add this workflow to your repository:

```yaml
# .github/workflows/sre-agent.yml
name: SRE Agent
on:
  workflow_run:
    workflows: ["*"]
    types: [completed]

jobs:
  analyze:
    if: ${{ github.event.workflow_run.conclusion == 'failure' }}
    runs-on: ubuntu-latest
    steps:
      - uses: Mrgig7/Autonomous-AI-powered-SRE-Agent@main
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

**That's it!** The agent will automatically analyze failed workflows.

---

## Option 2: Docker Compose (Self-Hosted)

```bash
# Clone the repository
git clone https://github.com/Mrgig7/Autonomous-AI-powered-SRE-Agent.git
cd Autonomous-AI-powered-SRE-Agent

# Copy environment file
cp .env.example .env

# Edit .env with your settings
# - Set GITHUB_WEBHOOK_SECRET
# - Set GITHUB_TOKEN
# - Configure notifications (optional)

# Start all services
docker-compose up -d

# Check status
docker-compose ps
```

The API will be available at: **http://localhost:8000**

### Configure Webhook

1. Go to your GitHub repository → Settings → Webhooks
2. Add webhook:
   - **URL:** `http://your-server:8000/webhooks/github`
   - **Secret:** Your `GITHUB_WEBHOOK_SECRET`
   - **Events:** Select "Workflow runs"

---

## Option 3: With Local LLM

For air-gapped environments or privacy:

```bash
# Start with Ollama included
docker-compose --profile local-llm up -d

# Pull the model
docker exec sre-agent-ollama ollama pull deepseek-coder:6.7b
```

---

## Verify Installation

```bash
# Check health
curl http://localhost:8000/health

# View API docs
open http://localhost:8000/docs
```

---

## Next Steps

- 📖 [Project README](./README.md)
- 🧭 [Pipeline Flow](./docs/pipeline.md)
- 📦 [Publishing](./docs/PUBLISHING.md)

---

## Troubleshooting

**Database connection failed:**
```bash
docker-compose logs postgres
```

**Worker not processing:**
```bash
docker-compose logs worker
```

**Need help?** Open an issue on GitHub.
