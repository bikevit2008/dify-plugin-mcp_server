# Privacy Policy

## Data Handling

This plugin processes data within your Dify instance. It does **not** store, retain, or transmit data to any third-party services.

- **Workflow inputs**: User-provided arguments are forwarded to the Dify apps you configure. Data remains within your Dify deployment.
- **Workflow outputs**: Responses from your Dify apps are returned to the MCP client. No data is logged or stored by this plugin beyond the duration of the request.
- **Authentication tokens**: Stored in Dify's encrypted settings storage. Never logged or exposed.

## Data Collection

- **No analytics**: We do not implement any analytics, tracking, or telemetry.
- **No usage monitoring**: We do not track or monitor how you use the plugin.
- **No personal data storage**: The plugin does not persist any user data between requests.

## Third-Party Services

This plugin does not integrate with external third-party services. All processing happens within your Dify instance and the Dify apps you configure. If your Dify apps call external APIs (e.g., Perplexity, OpenAI), those calls are subject to the respective services' privacy policies — not this plugin's.

## Session Data

MCP sessions are held in memory and automatically expire after the configured timeout. No session data is written to disk or shared between plugin instances.

## Changes

If there are any changes to our data handling practices, we will update this document accordingly.