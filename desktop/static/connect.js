// "Connect to a Team Server" window. Uses only the deskSetup bridge from the preload script.
const form = document.getElementById("form");
const input = document.getElementById("url");
const error = document.getElementById("error");
window.deskSetup.getConfig().then((c) => {
  if (c.serverUrl) input.value = c.serverUrl;
  if (c.serverOnly) document.getElementById("local").hidden = true;  // this edition has no local engine
});
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  error.textContent = "Checking…";
  const r = await window.deskSetup.setServer(input.value.trim());
  if (!r.ok) error.textContent = r.error;
});
document.getElementById("local").addEventListener("click", () => window.deskSetup.setServer(null));
