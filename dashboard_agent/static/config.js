// Point the SPA at your LangGraph deployment. Edit for your setup (or override
// live via the ⚙️ gear, which saves to localStorage).
//
//   assistantId A specific assistant UUID (from scripts/seed_assistants.py) OR the
//               graph id "dashboard_agent" to use that graph's default assistant.
//   apiKey      Only needed for a secured/cloud deployment (sent as x-api-key).
//
// The Agent Server base URL is not set here; it defaults to http://127.0.0.1:2024
// (override live via the ⚙️ gear, which saves to localStorage).
window.LG = {
  assistantId: "dashboard_agent",
  // apiKey: "lsv2_...",
};
