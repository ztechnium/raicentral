// "Official SDK" transport: sends the same provider-native request body that the
// direct-API approach builds, but through each provider's own SDK pointed at the RAI gateway.
//   anthropic            -> @anthropic-ai/sdk
//   openai / ollama      -> openai            (ollama is OpenAI-compatible)
//   azure-openai         -> openai (AzureOpenAI)
//   google-ai / vertex-ai-> @google/genai
// SDKs are imported lazily, so the direct approach keeps working without `npm install`.

const NO_KEY = "rai-gateway"; // SDKs insist on a key; the gateway ignores it and uses x-rai-agent-key.

const SDK_PACKAGE = {
  anthropic: "@anthropic-ai/sdk",
  openai: "openai",
  ollama: "openai",
  "azure-openai": "openai",
  "google-ai": "@google/genai",
  "vertex-ai": "@google/genai",
};

async function load(pkg) {
  try {
    return await import(pkg);
  } catch (e) {
    if (e.code === "ERR_MODULE_NOT_FOUND") throw new Error(`SDK approach needs "${pkg}". Run: npm install`);
    throw e;
  }
}

const headersToObject = (h) => {
  const out = {};
  h?.forEach?.((v, k) => { out[k] = v; });
  return out;
};

/**
 * @param {string} provider
 * @param {{path:string, body:object, headers:object}} req  request built by buildRequest()
 * @returns {Promise<{ok:boolean,status:number,json:any,headers:object,url:string,error?:string}>}
 */
export async function sdkCall({ provider, req, model, extra = {}, baseUrl, key }) {
  const auth = { "x-rai-agent-key": key };
  const timeout = 120_000;

  try {
    if (provider === "anthropic") {
      const { default: Anthropic } = await load("@anthropic-ai/sdk");
      // The SDK appends /v1/messages itself.
      const client = new Anthropic({ apiKey: NO_KEY, baseURL: `${baseUrl}/providers/anthropic`, defaultHeaders: auth, timeout, maxRetries: 0 });
      const { data, response } = await client.messages.create(req.body).withResponse();
      return { ok: true, status: response.status, json: data, headers: headersToObject(response.headers), url: response.url };
    }

    if (provider === "openai" || provider === "ollama") {
      const { default: OpenAI } = await load("openai");
      const client = new OpenAI({ apiKey: NO_KEY, baseURL: `${baseUrl}/providers/${provider}/v1`, defaultHeaders: auth, timeout, maxRetries: 0 });
      const { data, response } = await client.chat.completions.create(req.body).withResponse();
      return { ok: true, status: response.status, json: data, headers: headersToObject(response.headers), url: response.url };
    }

    if (provider === "azure-openai") {
      const { AzureOpenAI } = await load("openai");
      const client = new AzureOpenAI({
        apiKey: NO_KEY,
        baseURL: `${baseUrl}/providers/azure-openai/openai`,
        deployment: model,
        apiVersion: extra.apiVersion || "2024-10-21",
        defaultHeaders: auth,
        timeout,
        maxRetries: 0,
      });
      const { data, response } = await client.chat.completions.create({ ...req.body, model }).withResponse();
      return { ok: true, status: response.status, json: data, headers: headersToObject(response.headers), url: response.url };
    }

    if (provider === "google-ai" || provider === "vertex-ai") {
      const { GoogleGenAI } = await load("@google/genai");
      const vertex = provider === "vertex-ai";
      const project = extra.project || process.env.GCP_PROJECT_ID;
      if (vertex && !project) throw new Error("Vertex AI needs a GCP project id (set it in the UI or GCP_PROJECT_ID).");
      const httpOptions = { baseUrl: `${baseUrl}/providers/genai`, headers: auth, timeout };
      const client = vertex
        ? new GoogleGenAI({
            vertexai: true,
            project,
            location: extra.location || "global",
            // The gateway attaches the real Google token; give the SDK a no-op auth client
            // so it does not go looking for local application-default credentials.
            googleAuthOptions: { authClient: { getRequestHeaders: async () => new Headers() } },
            httpOptions,
          })
        : new GoogleGenAI({ apiKey: NO_KEY, httpOptions });
      const b = req.body;
      const res = await client.models.generateContent({
        model,
        contents: b.contents,
        config: { ...(b.generationConfig ?? {}), ...(b.systemInstruction ? { systemInstruction: b.systemInstruction } : {}), ...(b.tools ? { tools: b.tools } : {}) },
      });
      // GenerateContentResponse is JSON-shaped like the REST response.
      return { ok: true, status: 200, json: JSON.parse(JSON.stringify(res)), headers: {}, url: `${baseUrl}/providers/genai (${SDK_PACKAGE[provider]})` };
    }

    throw new Error(`Unknown provider: ${provider}`);
  } catch (e) {
    // SDK APIError carries status/headers/error body; Google's ApiError carries status.
    const status = typeof e.status === "number" ? e.status : e.cause ? 502 : 400;
    const body = e.error ?? null;
    return {
      ok: false,
      status,
      json: body,
      headers: e.headers && typeof e.headers.forEach === "function" ? headersToObject(e.headers) : (e.headers ?? {}),
      url: `${baseUrl}/providers/${provider}`,
      error: body?.error?.message ?? body?.message ?? e.message,
    };
  }
}
