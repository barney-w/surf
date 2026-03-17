import { type Configuration, LogLevel } from "@azure/msal-browser";

const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID ?? "";

export const msalConfig: Configuration = {
  auth: {
    clientId,
    authority: "https://login.microsoftonline.com/common",
    redirectUri: window.location.origin,
    postLogoutRedirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: "sessionStorage",
    storeAuthStateInCookie: false,
  },
  system: {
    loggerOptions: {
      logLevel: LogLevel.Warning,
      piiLoggingEnabled: false,
    },
  },
};

// Scopes for silent SSO
export const loginScopes = ["User.Read"];

// Scope for calling our backend API (OBO)
export const apiScope = clientId ? `api://${clientId}/access_as_user` : "";
