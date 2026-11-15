export const BUSINESS_UNITS = [
  "Procurement",
  "Finance",
  "Human Resources",
  "Customer Support",
  "Legal & Compliance",
  "Information Technology",
  "Operations",
  "Fraud & Risk",
  "Engineering",
] as const;

export const RISK_TIERS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;

export const RESOURCE_TYPES = [
  { value: "tool", label: "Tool" },
  { value: "mcp_server", label: "MCP Server" },
  { value: "data_source", label: "Data Source" },
  { value: "model", label: "Model" },
  { value: "external_service", label: "External Service" },
] as const;

/**
 * Provider identifiers understood by the provider gateway adapters.
 * `value` must equal the adapter `provider_name` so gateway requests resolve to
 * a registered model (see provider_gateway/proxy.py resolve_model_id).
 */
export const GATEWAY_PROVIDERS = [
  { value: "openai", label: "OpenAI" },
  { value: "azure_openai", label: "Azure OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "google_ai", label: "Google AI (Gemini)" },
  { value: "vertex", label: "Google Vertex AI" },
  { value: "bedrock", label: "AWS Bedrock" },
  { value: "ollama", label: "Ollama" },
] as const;
