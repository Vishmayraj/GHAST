/* source: "sample" uses built-in sample data. "live" calls the API at apiBase.
   Override for a single visit with ?source=live. The Docker image sets source to "live" and
   apiBase to "/api" (frontend/Dockerfile); nginx adds the API key. */
window.GHAST_CONFIG = { source: "sample", apiBase: "", apiKey: "" };
