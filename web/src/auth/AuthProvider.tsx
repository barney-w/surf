import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  type AccountInfo,
  InteractionRequiredAuthError,
  PublicClientApplication,
} from "@azure/msal-browser";
import { msalConfig, loginScopes, apiScope } from "./authConfig";
import { needsPopupAuth, getApiBase, isTauri } from "./platform";
import { restoreMsalCache, persistMsalCache, clearMsalCache } from "./tauriTokenCache";

interface UserProfile {
  displayName: string;
  givenName: string | null;
  department: string | null;
  jobTitle: string | null;
  mail: string | null;
  photoUrl: string | null;
  groups: string[];
}

interface AuthState {
  isLoading: boolean;
  isAuthenticated: boolean;
  account: AccountInfo | null;
  profile: UserProfile | null;
  photoUrl: string | null;
  error: string | null;
  login: () => void;
  logout: () => void;
  getApiToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthState>({
  isLoading: true,
  isAuthenticated: false,
  account: null,
  profile: null,
  photoUrl: null,
  error: null,
  login: () => {},
  logout: () => {},
  getApiToken: async () => null,
});

export function useAuth() {
  return useContext(AuthContext);
}

const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID ?? "";

// MSAL instance is created lazily — in Tauri we need to restore the
// persisted token cache into localStorage before MSAL reads it.
let msalInstance: PublicClientApplication | null = null;
let msalReady: Promise<PublicClientApplication | null> | null = null;

function getMsalInstance(): Promise<PublicClientApplication | null> {
  if (!clientId) return Promise.resolve(null);
  if (msalReady) return msalReady;
  msalReady = (async () => {
    await restoreMsalCache();
    msalInstance = new PublicClientApplication(msalConfig);
    return msalInstance;
  })();
  return msalReady;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(!!clientId);
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Fetch user profile from our backend
  const fetchProfile = useCallback(async (token: string) => {
    const apiBase = getApiBase();
    try {
      const resp = await fetch(`${apiBase}/me`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) return;
      const data = (await resp.json()) as UserProfile;
      setProfile(data);

      // Fetch photo separately — it may 404
      try {
        const photoResp = await fetch(`${apiBase}/me/photo`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (photoResp.ok) {
          const blob = await photoResp.blob();
          setPhotoUrl(URL.createObjectURL(blob));
        }
      } catch {
        // No photo — fine
      }
    } catch {
      // Profile fetch failed — continue with JWT claims
    }
  }, []);

  // Initialize MSAL and attempt silent SSO
  useEffect(() => {
    if (!clientId) {
      setIsLoading(false);
      return;
    }

    const init = async () => {
      try {
        const msal = await getMsalInstance();
        if (!msal) {
          setIsLoading(false);
          return;
        }
        await msal.initialize();

        // Handle redirect response (if returning from loginRedirect)
        const redirectResult = await msal.handleRedirectPromise();
        if (redirectResult?.account) {
          setAccount(redirectResult.account);
          msal.setActiveAccount(redirectResult.account);
          setIsLoading(false);
          // Acquire API-scoped token (redirect token has User.Read audience)
          try {
            const tokenResult = await msal.acquireTokenSilent({
              scopes: [apiScope],
              account: redirectResult.account,
            });
            void persistMsalCache();
            void fetchProfile(tokenResult.accessToken);
          } catch {
            // Silent token acquisition failed — profile won't load
          }
          return;
        }

        // Check for existing account in cache
        const accounts = msal.getAllAccounts();
        if (accounts.length > 0) {
          msal.setActiveAccount(accounts[0]);
          setAccount(accounts[0]);
          setIsLoading(false);

          // Get token for profile fetch
          try {
            const tokenResult = await msal.acquireTokenSilent({
              scopes: [apiScope],
              account: accounts[0],
            });
            void persistMsalCache();
            void fetchProfile(tokenResult.accessToken);
          } catch {
            // Token refresh failed — user is still "authenticated" from cache
          }
          return;
        }

        // Try ssoSilent (hidden iframe checks for existing Entra session)
        // Skip in Tauri — iframe-based SSO is blocked by WebView policies
        if (!isTauri()) {
          try {
            const ssoResult = await msal.ssoSilent({
              scopes: loginScopes,
            });
            if (ssoResult.account) {
              setAccount(ssoResult.account);
              msal.setActiveAccount(ssoResult.account);
              setIsLoading(false);

              const tokenResult = await msal.acquireTokenSilent({
                scopes: [apiScope],
                account: ssoResult.account,
              });
              void fetchProfile(tokenResult.accessToken);
              return;
            }
          } catch {
            // ssoSilent failed — user will see the "Sign in" button
          }
        }

        // No silent auth possible
        setIsLoading(false);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Authentication failed");
        setIsLoading(false);
      }
    };

    void init();
  }, [fetchProfile]);

  const login = useCallback(async () => {
    if (!msalInstance) return;
    if (needsPopupAuth()) {
      try {
        const result = await msalInstance.loginPopup({ scopes: loginScopes });
        if (result.account) {
          setAccount(result.account);
          msalInstance.setActiveAccount(result.account);
          const tokenResult = await msalInstance.acquireTokenSilent({
            scopes: [apiScope],
            account: result.account,
          });
          void persistMsalCache();
          void fetchProfile(tokenResult.accessToken);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Login failed');
      }
    } else {
      void msalInstance.loginRedirect({ scopes: loginScopes });
    }
  }, [fetchProfile]);

  const logout = useCallback(async () => {
    if (!msalInstance) return;
    setAccount(null);
    setProfile(null);
    if (photoUrl) URL.revokeObjectURL(photoUrl);
    setPhotoUrl(null);
    void clearMsalCache();
    if (needsPopupAuth()) {
      await msalInstance.logoutPopup();
    } else {
      void msalInstance.logoutRedirect();
    }
  }, [photoUrl]);

  const getApiToken = useCallback(async (): Promise<string | null> => {
    if (!msalInstance || !account) return null;
    try {
      const result = await msalInstance.acquireTokenSilent({
        scopes: [apiScope],
        account,
      });
      void persistMsalCache();
      return result.accessToken;
    } catch (err) {
      // In Tauri, any token failure should attempt popup re-auth before
      // giving up — silent renewal often fails due to WebView limitations.
      if (err instanceof InteractionRequiredAuthError || needsPopupAuth()) {
        if (needsPopupAuth()) {
          try {
            const result = await msalInstance.acquireTokenPopup({ scopes: [apiScope] });
            void persistMsalCache();
            return result.accessToken;
          } catch {
            // Popup was closed or failed — clear auth state so UI shows login
            setAccount(null);
            setProfile(null);
            void clearMsalCache();
            return null;
          }
        }
        void msalInstance.acquireTokenRedirect({ scopes: [apiScope] });
        return null;
      }
      // Non-interaction error (e.g. network failure, cache cleared) —
      // clear auth state so the user can re-login
      setAccount(null);
      setProfile(null);
      return null;
    }
  }, [account]);

  const value = useMemo<AuthState>(
    () => ({
      isLoading,
      isAuthenticated: !!account,
      account,
      profile,
      photoUrl,
      error,
      login,
      logout,
      getApiToken,
    }),
    [isLoading, account, profile, photoUrl, error, login, logout, getApiToken],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
