const clientId = process.env.EXPO_PUBLIC_ENTRA_CLIENT_ID ?? "";
const tenantId = process.env.EXPO_PUBLIC_ENTRA_TENANT_ID ?? "";

// Entra ID OAuth 2.0 endpoints
export const discovery = {
  authorizationEndpoint: `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/authorize`,
  tokenEndpoint: `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/token`,
  revocationEndpoint: `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/logout`,
};

export { clientId };

// Scopes for initial login (profile + ID token)
export const loginScopes = ["openid", "profile", "User.Read", "offline_access"];

// Scope for calling our backend API (OBO flow)
export const apiScope = clientId ? `api://${clientId}/access_as_user` : "";

// All scopes requested during authorization (login + API in one request)
export const allScopes = clientId
  ? [...loginScopes, apiScope]
  : loginScopes;

export const isAuthConfigured = !!clientId;

// Secure store keys for token persistence
export const TOKEN_KEYS = {
  accessToken: "surf_access_token",
  refreshToken: "surf_refresh_token",
  tokenExpiry: "surf_token_expiry",
  idToken: "surf_id_token",
} as const;
