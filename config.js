/* Frontdesk AI Receptionist — runtime config.
   The backend deploy overwrites this file to point the static frontend at the
   live API without requiring a frontend rebuild. Leave the value as an empty
   string for local demo mode (the dashboard falls back to built-in sample data). */
window.__API_BASE__ = window.__API_BASE__ || "https://ai-receptionist-loongpancoasts-projects.vercel.app";
