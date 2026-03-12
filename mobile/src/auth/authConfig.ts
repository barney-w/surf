const clientId = process.env.EXPO_PUBLIC_ENTRA_CLIENT_ID ?? "";
const tenantId = process.env.EXPO_PUBLIC_ENTRA_TENANT_ID ?? "";

// Entra ID OAuth 2.0 endpoints
export const discovery = {
  authorizationEndpoint: `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/authorize`,
  tokenEndpoint: `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/token`,
  revocationEndpoint: `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/logout`,
};

export { clientId };

// Scopes for initial login — only identity scopes, no resource-specific scopes.
// Mixing Graph scopes (User.Read) with custom API scopes in one request causes
// Entra to issue a Graph-audience token, which the API backend rejects.
export const loginScopes = ["openid", "profile", "offline_access"];

// Scope for calling our backend API — acquired via a separate token request
// using the refresh token so the audience is api://{clientId}.
export const apiScope = clientId ? `api://${clientId}/access_as_user` : "";

export const isAuthConfigured = !!clientId;

// Secure store keys for token persistence
export const TOKEN_KEYS = {
  accessToken: "surf_access_token",
  refreshToken: "surf_refresh_token",
  tokenExpiry: "surf_token_expiry",
  idToken: "surf_id_token",
} as const;
