# AI-Agent

Just my WhatsApp Business AI assistant.

## WhatsApp AI Agent

A transparent WhatsApp Business assistant that can answer from:

- your product/service catalog
- approved conversation memory stored locally
- optional web search results
- optional OpenAI reasoning

This project uses the official WhatsApp Cloud API webhook flow. It is designed for legitimate business automation with audit logs, user opt-out, human handoff, and clear assistant disclosure. It does not try to hide that automation is being used.

## Quick Start

1. Copy `.env.example` to `.env` and fill in your WhatsApp Cloud API values.
2. Add your catalog content to `data/catalog.md`.
3. Start the local server:

```powershell
.\run_agent.ps1
```

4. Open `http://localhost:8080/` to use the OrderBot simulation console.
5. Expose `http://localhost:8080/webhook` with a tunnel such as ngrok or Cloudflare Tunnel.
6. Put the public webhook URL and verify token into your Meta WhatsApp app webhook settings.

## Environment

```text
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_VERIFY_TOKEN=choose-a-random-token
OPENAI_API_KEY=optional
OPENAI_MODEL=gpt-4.1-mini
BRAVE_SEARCH_API_KEY=optional
AGENT_DISCLOSURE=on
HUMAN_HANDOFF_NUMBER=optional
```

## How It Learns

The agent stores summarized facts from conversations in `data/agent.sqlite3`. It uses those facts as retrieval memory in future conversations. It does not rewrite its own code or silently change policies. That keeps the system inspectable and safer to operate.

Users can send:

- `stop` to opt out
- `human` to request handoff
- `forget me` to delete their stored memory

## Files

- `agent.py` - webhook server and WhatsApp API integration
- `orderbot.py` - retail sales workflow, distributor checks, quote generation, payment and pickup state
- `brain.py` - retrieval, answer generation, and memory learning
- `storage.py` - SQLite persistence
- `config.py` - environment loading
- `data/catalog.md` - your business/catalog facts
- `data/distributors.json` - category-specific distributor priority routes
- `.env.example` - configuration template

## Distributor Routing

Edit `data/distributors.json` to rename the placeholder distributors to your real WhatsApp contact labels. The order matters: OrderBot asks the first distributor in the matched category, then moves to the next one if the supplier says `no stock`, `out of stock`, `unavailable`, `not available`, or `sold out`.

Current routes:

- `phones` - 4 phone distributors
- `accessories` - 7 accessory distributors
- `laptops` - 10 laptop distributors
- `oraimo_accessories` - dedicated Oraimo supplier
- `amaya_accessories` - dedicated Amaya supplier
- `infinix_accessories` - dedicated Infinix accessories supplier
- `logitech` - dedicated Logitech supplier

## Production Notes

- Use HTTPS for the webhook.
- Keep access tokens in environment variables only.
- Review logs and memory regularly.
- For high-stakes topics such as legal, medical, or financial advice, use human handoff.
- Make sure customers know they may be interacting with automation.
