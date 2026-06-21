# BharatCode 🇮🇳

**AI Coding Agent for Indian Developers — powered by DeepSeek**

## Install

```bash
pip install bharatcode
```

## Setup

```bash
bharatcode config --key YOUR_DEEPSEEK_KEY
```

Or set via environment variable:

```bash
DEEPSEEK_API_KEY=sk-...
```

## Usage

```bash
cd /your/project
bharatcode
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DEEPSEEK_API_KEY` | ✅ | DeepSeek API key |
| `BHARATCODE_MODEL` | No | Override model (`deepseek-v4-pro` / `deepseek-v4-flash`) |
| `BHARATCODE_DEBUG` | No | Set to `1` for full tracebacks |
| `BHARATCODE_AUTO_APPROVE` | No | Set to `1` to skip all permission prompts |
