// Point the SPA at your LangGraph deployment. Edit for your setup (or override
// live via the ⚙️ gear, which saves to localStorage).
//
//   url         Agent Server base URL. `langgraph dev` = http://127.0.0.1:2024
//   assistantId A specific assistant UUID (from scripts/seed_assistants.py) OR the
//               graph id "dashboard_agent" to use that graph's default assistant.
//   apiKey      Only needed for a secured/cloud deployment (sent as x-api-key).
window.LG = {
  url: "http://127.0.0.1:2024",
  assistantId: "dashboard_agent",
  // apiKey: "lsv2_...",
};
