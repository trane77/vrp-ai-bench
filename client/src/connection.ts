export function endpoint() {
  return sessionStorage.getItem("vrp-endpoint") || location.origin;
}

export function token() {
  return sessionStorage.getItem("vrp-token") || "";
}

export function apiUrl(path: string) {
  return new URL(path, endpoint()).toString();
}

export async function request(path: string, method = "GET", body?: unknown) {
  const remote = endpoint() !== location.origin;
  if (remote && !token()) throw Error("Inserisci il token Ai360 nelle impostazioni di connessione.");
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      method,
      headers: { "Content-Type": "application/json", ...(token() ? { "X-VRP-Token": token() } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw Error("Connessione non riuscita. Verifica endpoint HTTPS, rete e certificato attendibile.");
  }
  if (response.status === 401) throw Error("Token Ai360 mancante o rifiutato.");
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: response.statusText }));
    throw Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  }
  return response;
}

export function streamUrl(path: string) {
  const url = new URL(apiUrl(path));
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  if (token()) url.searchParams.set("token", token());
  return url.toString();
}
