import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type PropsWithChildren,
} from "react";
import * as AuthSession from "expo-auth-session";
import * as SecureStore from "expo-secure-store";
import * as WebBrowser from "expo-web-browser";

import {
  discovery,
  clientId,
  loginScopes,
  apiScope,
  isAuthConfigured,
  TOKEN_KEYS,
} from "./authConfig";
import { getApiUrl } from "../utils/apiUrl";

WebBrowser.maybeCompleteAuthSession();

interface UserProfile {
  displayName: string;
  givenName: string | null;
  department: string | null;
  jobTitle: string | null;
  mail: string | null;
  photoUrl: string | null;
  groups: string[];
}

interface Account {
  name: string | null;
  email: string | null;
}

interface AuthState {
  isLoading: boolean;
  isAuthenticated: boolean;
  account: Account | null;
  profile: UserProfile | null;
  photoUrl: string | null;
  error: string | null;
  login: () => void;
  logout: () => void;
  getApiToken: () => Promise<string | null>;
}

const defaultState: AuthState = {
  isLoading: false,
  isAuthenticated: false,
  account: null,
  profile: null,
  photoUrl: null,
  error: null,
  login: () => {},
  logout: () => {},
  getApiToken: async () => null,
};

const AuthContext = createContext<AuthState>(defaultState);

export function useAuth() {
  return useContext(AuthContext);
}

// --- Token management helpers ---

async function saveTokens(
  accessToken: string,
  refreshToken: string | null,
  expiresIn: number,
  idToken?: string,
) {
  const expiry = String(Date.now() + expiresIn * 1000);
  await SecureStore.setItemAsync(TOKEN_KEYS.accessToken, accessToken);
  await SecureStore.setItemAsync(TOKEN_KEYS.tokenExpiry, expiry);
  if (refreshToken) {
    await SecureStore.setItemAsync(TOKEN_KEYS.refreshToken, refreshToken);
  }
  if (idToken) {
    await SecureStore.setItemAsync(TOKEN_KEYS.idToken, idToken);
  }
}

async function loadTokens() {
  const accessToken = await SecureStore.getItemAsync(TOKEN_KEYS.accessToken);
  const refreshToken = await SecureStore.getItemAsync(TOKEN_KEYS.refreshToken);
  const expiryStr = await SecureStore.getItemAsync(TOKEN_KEYS.tokenExpiry);
  if (!accessToken || !expiryStr) return null;
  return { accessToken, refreshToken, expiry: Number(expiryStr) };
}

async function clearTokens() {
  await Promise.all(
    Object.values(TOKEN_KEYS).map((key) => SecureStore.deleteItemAsync(key)),
  );
}

async function refreshAccessToken(refreshToken: string, scopes: string[]) {
  try {
    const params = new URLSearchParams({
      grant_type: "refresh_token",
      client_id: clientId,
      refresh_token: refreshToken,
      scope: scopes.join(" "),
    });
    const resp = await fetch(discovery.tokenEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: params.toString(),
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    return {
      accessToken: data.access_token as string,
      refreshToken: (data.refresh_token as string) ?? refreshToken,
      expiresIn: data.expires_in as number,
      idToken: data.id_token as string | undefined,
    };
  } catch {
    return null;
  }
}

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const payload = token.split(".")[1];
    // Base64url decode
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const json = atob(base64);
    return JSON.parse(json);
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: PropsWithChildren) {
  const [isLoading, setIsLoading] = useState(true);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [account, setAccount] = useState<Account | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const redirectUri = AuthSession.makeRedirectUri({
    scheme: "surf",
    path: "auth/callback",
  });

  // Log the redirect URI in dev mode so developers know what to register in Azure
  useEffect(() => {
    if (__DEV__) {
      console.log("[Auth] Redirect URI:", redirectUri);
    }
  }, [redirectUri]);

  const [request, response, promptAsync] = AuthSession.useAuthRequest(
    {
      clientId,
      scopes: loginScopes,
      redirectUri,
      usePKCE: true,
      responseType: AuthSession.ResponseType.Code,
    },
    discovery,
  );

  const fetchProfile = useCallback(async (token: string) => {
    const apiUrl = getApiUrl();
    try {
      const resp = await fetch(`${apiUrl}/me`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) return;
      const data = (await resp.json()) as UserProfile;
      setProfile(data);

      // Fetch photo — convert to base64 data URI (no URL.createObjectURL in RN)
      try {
        const photoResp = await fetch(`${apiUrl}/me/photo`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (photoResp.ok) {
          const buffer = await photoResp.arrayBuffer();
          const bytes = new Uint8Array(buffer);
          let binary = "";
          for (let i = 0; i < bytes.length; i++) {
            binary += String.fromCharCode(bytes[i]);
          }
          const base64 = btoa(binary);
          setPhotoUrl(`data:image/jpeg;base64,${base64}`);
        }
      } catch {
        // Photo fetch is optional
      }
    } catch {
      // Profile fetch is optional
    }
  }, []);

  const setAuthenticatedState = useCallback(
    (tokens: { idToken?: string; refreshToken?: string | null }) => {
      setIsAuthenticated(true);
      setError(null);

      // Parse ID token for account info
      if (tokens.idToken) {
        const claims = decodeJwtPayload(tokens.idToken);
        if (claims) {
          setAccount({
            name: (claims.name as string) ?? null,
            email: (claims.preferred_username as string) ?? null,
          });
        }
      }

      // Fetch profile using an API-scoped token
      if (tokens.refreshToken) {
        void (async () => {
          const apiTokenResult = await refreshAccessToken(
            tokens.refreshToken!,
            [apiScope],
          );
          if (apiTokenResult) {
            await saveTokens(
              apiTokenResult.accessToken,
              apiTokenResult.refreshToken,
              apiTokenResult.expiresIn,
            );
            void fetchProfile(apiTokenResult.accessToken);
          }
        })();
      }
    },
    [fetchProfile],
  );

  // Attempt silent auth on mount
  useEffect(() => {
    if (!isAuthConfigured) {
      setIsLoading(false);
      return;
    }

    void (async () => {
      try {
        const tokens = await loadTokens();
        if (!tokens) {
          setIsLoading(false);
          return;
        }

        // If we have a valid API-scoped access token, we're good
        if (tokens.expiry > Date.now()) {
          setAuthenticatedState({ refreshToken: tokens.refreshToken });
          setIsLoading(false);
          return;
        }

        // Try refresh to get a new API-scoped token
        if (tokens.refreshToken) {
          const refreshed = await refreshAccessToken(tokens.refreshToken, [apiScope]);
          if (refreshed) {
            await saveTokens(
              refreshed.accessToken,
              refreshed.refreshToken,
              refreshed.expiresIn,
              refreshed.idToken,
            );
            setAuthenticatedState({
              idToken: refreshed.idToken,
              refreshToken: refreshed.refreshToken,
            });
            setIsLoading(false);
            return;
          }
        }

        // Tokens expired and refresh failed
        await clearTokens();
        setIsLoading(false);
      } catch {
        setIsLoading(false);
      }
    })();
  }, [setAuthenticatedState]);

  // Handle auth response (success, cancel, error, etc.)
  useEffect(() => {
    if (!response) return;

    if (response.type === "cancel" || response.type === "dismiss") {
      setIsLoading(false);
      return;
    }

    if (response.type === "error") {
      setError(response.error?.message ?? "Authentication error");
      setIsLoading(false);
      return;
    }

    if (response.type !== "success" || !request?.codeVerifier) return;

    void (async () => {
      try {
        const { code } = response.params;
        const params = new URLSearchParams({
          grant_type: "authorization_code",
          code,
          client_id: clientId,
          redirect_uri: redirectUri,
          code_verifier: request.codeVerifier!,
          scope: loginScopes.join(" "),
        });

        const tokenResp = await fetch(discovery.tokenEndpoint, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: params.toString(),
        });

        if (!tokenResp.ok) {
          if (__DEV__) {
            const errBody = await tokenResp.text().catch(() => "");
            console.error("[Auth] Token exchange failed:", tokenResp.status, errBody);
          }
          setError("Token exchange failed");
          setIsLoading(false);
          return;
        }

        const data = await tokenResp.json();
        const refreshToken = (data.refresh_token as string) ?? null;

        // The login token is identity-scoped (audience: Graph).
        // Now acquire an API-scoped token using the refresh token.
        if (refreshToken && apiScope) {
          const apiTokenResult = await refreshAccessToken(refreshToken, [apiScope]);
          if (apiTokenResult) {
            await saveTokens(
              apiTokenResult.accessToken,
              apiTokenResult.refreshToken,
              apiTokenResult.expiresIn,
            );

          } else {
            // Fall back to saving the login token if API token acquisition fails
            await saveTokens(data.access_token, refreshToken, data.expires_in);
            if (__DEV__) {
              console.warn("[Auth] Could not acquire API-scoped token. API calls may fail.");
            }
          }
        } else {
          await saveTokens(data.access_token, refreshToken, data.expires_in, data.id_token);
        }

        setAuthenticatedState({
          idToken: data.id_token,
          refreshToken,
        });
      } catch (e) {
        if (__DEV__) {
          console.error("[Auth] Authentication failed:", e);
        }
        setError("Authentication failed");
      } finally {
        setIsLoading(false);
      }
    })();
  }, [response, request, redirectUri, setAuthenticatedState]);

  const login = useCallback(() => {
    void promptAsync();
  }, [promptAsync]);

  const logout = useCallback(async () => {
    await clearTokens();
    setIsAuthenticated(false);
    setAccount(null);
    setProfile(null);
    setPhotoUrl(null);
    setError(null);
  }, []);

  const getApiToken = useCallback(async (): Promise<string | null> => {
    const tokens = await loadTokens();
    if (!tokens) return null;

    if (tokens.expiry > Date.now()) {
      return tokens.accessToken;
    }

    if (tokens.refreshToken) {
      const refreshed = await refreshAccessToken(tokens.refreshToken, [apiScope]);
      if (refreshed) {
        await saveTokens(
          refreshed.accessToken,
          refreshed.refreshToken,
          refreshed.expiresIn,
        );
        return refreshed.accessToken;
      }
    }

    setError("Session expired. Please sign in again.");
    return null;
  }, []);

  if (!isAuthConfigured) {
    return (
      <AuthContext.Provider value={defaultState}>{children}</AuthContext.Provider>
    );
  }

  return (
    <AuthContext.Provider
      value={{
        isLoading,
        isAuthenticated,
        account,
        profile,
        photoUrl,
        error,
        login,
        logout,
        getApiToken,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
