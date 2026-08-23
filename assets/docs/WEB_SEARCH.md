# Web Search for Claude Desktop (Amazon Bedrock AgentCore)

This guide covers the optional web search capability for **Claude Desktop** (Cowork) in third-party platform mode on Amazon Bedrock. It deploys an **Amazon Bedrock AgentCore Gateway** with the fully managed **Web Search connector** and exposes it to Claude Desktop as a managed MCP server.

## What it does

The [Web Search tool on Amazon Bedrock AgentCore](https://aws.amazon.com/blogs/aws/announcing-web-search-on-amazon-bedrock-agentcore-ground-your-ai-agents-in-current-accurate-web-knowledge/) is a managed, MCP‑compliant connector backed by Amazon's own web index. It returns titles, URLs, snippets, and publication dates so the model can ground answers in current information. There is no third‑party search API to provision and no outbound credentials to manage — queries stay within AWS.

The gateway stack provisions:

- An **AgentCore Gateway** (MCP protocol) whose inbound authorization reuses your existing identity provider — the same one the rest of this solution already uses.
- A **Gateway target** configured with the managed `web-search` connector (optional domain denylist).
- A least‑privilege **gateway execution IAM role** (`GetGateway`, `GetConfigurationBundleVersion`, `InvokeWebSearch`, `InvokeGateway`).

## Prerequisites

- This solution already deployed with an OIDC identity provider (Amazon Cognito, Microsoft Entra ID, Okta, Auth0, Google, or generic OIDC). The web search gateway reuses it for inbound auth.
- Deployment into a region where the Web Search connector is available — **`us-east-1` only** at time of writing. The `ccwb` tool deploys the gateway there automatically, regardless of your other stacks' region.

> **IAM Identity Center:** the gateway template supports an IAM (SigV4) authorization mode (`AuthType=idc`), but the `ccwb` workflow does not offer web search for IDC deployments — Claude Desktop does not yet support SigV4 authentication for MCP servers.

## Setting it up with `ccwb` (recommended)

Web search is opt-in (default off) and fully integrated into the CLI:

```bash
poetry run ccwb init               # answer Yes to "Enable web search?"
poetry run ccwb deploy websearch   # deploys the gateway; the endpoint is saved to your profile
poetry run ccwb package            # or: poetry run ccwb cowork generate
```

`ccwb package` and `ccwb cowork generate` automatically inject a managed MCP server named **`agentcore-websearch`** into the generated Claude Desktop MDM config (`.json` / `.mobileconfig` / `.reg`), and the installer places a small `websearch-headers` helper next to `credential-process`. The helper runs `credential-process --get-mcp-auth-header` to attach a Bearer id_token to each gateway request — no manual wiring and no OAuth client configuration in the MDM profile.

See [Web search via AgentCore Gateway](COWORK_3P.md#web-search-via-agentcore-gateway) in the Claude Desktop (Cowork 3P) guide for the generated configuration, how the helper authenticates, and the Web Fetch / egress-allowlist considerations.

## Deploying the template standalone (advanced)

If you manage infrastructure outside `ccwb`, you can deploy the gateway template directly. Note that the template creates a named IAM role, so `CAPABILITY_NAMED_IAM` is required:

```bash
aws cloudformation deploy \
  --region us-east-1 \
  --stack-name <your-stack-name> \
  --template-file deployment/infrastructure/bedrock-agentcore-gateway.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      AuthType=oidc \
      DiscoveryUrl=https://cognito-idp.<idp-region>.amazonaws.com/<user-pool-id>/.well-known/openid-configuration \
      ClientId=<your-app-client-id>
```

### Parameters

| Parameter | Description |
|-----------|-------------|
| `AuthType` | `oidc` (default) — validates the inbound id_token via a `CUSTOM_JWT` authorizer. `idc` — authorizes via IAM credentials (SigV4) for IAM Identity Center deployments. |
| `DiscoveryUrl` | Your IdP's OIDC discovery URL, ending with `/.well-known/openid-configuration`. Required when `AuthType=oidc`, ignored for `idc`. |
| `ClientId` | Your OIDC client ID. The gateway validates the id_token's `aud` claim against this value (an OIDC id_token carries `aud = client_id` for every provider). Required when `AuthType=oidc`, ignored for `idc`. |
| `DomainExcludeList` | Optional. Comma‑separated domains to exclude from search results. |

The stack output **`GatewayMcpEndpoint`** is the MCP endpoint URL (it already includes the `/mcp` path). When you deploy through `ccwb deploy websearch` instead, this endpoint is saved to your profile automatically and picked up by `ccwb package` / `ccwb cowork generate`.

## Data residency

> ⚠️ Web search queries (and fragments of user prompts) are processed by the managed connector in **`us-east-1`**, regardless of where the user's session runs or where Bedrock inference happens. Organizations with data residency or sovereignty obligations (e.g. GDPR) should evaluate whether this is acceptable before enabling web search.

## Cost

Web Search on Amazon Bedrock AgentCore is usage‑based: **$7 per 1,000 search queries** at time of writing (the gateway itself has no fixed hourly charge). See the [Amazon Bedrock AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/) page for current pricing. New AWS customers may receive Free Tier credits.

## References

- [Announcing Web Search on Amazon Bedrock AgentCore](https://aws.amazon.com/blogs/aws/announcing-web-search-on-amazon-bedrock-agentcore-ground-your-ai-agents-in-current-accurate-web-knowledge/)
- [AgentCore Gateway documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Claude Desktop (Cowork 3P) Guide](COWORK_3P.md)
