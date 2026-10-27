// Shown when the Team Server cannot be reached. Uses only the deskSetup bridge.
const server = new URLSearchParams(location.search).get("server") || "";
document.getElementById("server").textContent = server;
document.getElementById("retry").addEventListener("click", () => window.deskSetup.retry());
document.getElementById("settings").addEventListener("click", () => window.deskSetup.connect());
