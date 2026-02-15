# frontend

React + TypeScript GUI seed for FiPro_1.

## Routes

- `/startup`: startup config (provider/model select, readonly base_url/key status)
- `/generate`: report generation form
- `/backtest`: batch backtest page (async job + progress + cancel)
- `/proposals`: LLM proposal run review page (list + details)
- `/champion-health`: champion health check + watchdog alerts(ACK/关闭) + auto ticket view + optional auto-rollback page
- `/datasources`: data source status overview
- `/results/:reportId`: report details and raw JSON view

## Dev

```bash
npm install
npm run dev
```

Backend defaults to `http://127.0.0.1:8000` via Vite proxy.
