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

// Only create MSAL instance when client ID is configured
let msalInstance: PublicClientApplication | null = null;
if (clientId) {
  msalInstance = new PublicClientApplication(msalConfig);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(!!clientId);
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Fetch user profile from our backend
  const fetchProfile = useCallback(async (token: string) => {
    const apiBase = import.meta.env.VITE_SURF_API_URL || "/api/v1";
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
    if (!msalInstance) {
      setIsLoading(false);
      return;
    }

    const init = async () => {
      try {
        await msalInstance!.initialize();

        // Handle redirect response (if returning from loginRedirect)
        const redirectResult = await msalInstance!.handleRedirectPromise();
        if (redirectResult?.account) {
          setAccount(redirectResult.account);
          msalInstance!.setActiveAccount(redirectResult.account);
          setIsLoading(false);
          // Acquire API-scoped token (redirect token has User.Read audience)
          try {
            const tokenResult = await msalInstance!.acquireTokenSilent({
              scopes: [apiScope],
              account: redirectResult.account,
            });
            void fetchProfile(tokenResult.accessToken);
          } catch {
            // Silent token acquisition failed — profile won't load
          }
          return;
        }

        // Check for existing account in cache
        const accounts = msalInstance!.getAllAccounts();
        if (accounts.length > 0) {
          msalInstance!.setActiveAccount(accounts[0]);
          setAccount(accounts[0]);
          setIsLoading(false);

          // Get token for profile fetch
          try {
            const tokenResult = await msalInstance!.acquireTokenSilent({
              scopes: [apiScope],
              account: accounts[0],
            });
            void fetchProfile(tokenResult.accessToken);
          } catch {
            // Token refresh failed — user is still "authenticated" from cache
          }
          return;
        }

        // Try ssoSilent (hidden iframe checks for existing Entra session)
        try {
          const ssoResult = await msalInstance!.ssoSilent({
            scopes: loginScopes,
          });
          if (ssoResult.account) {
            setAccount(ssoResult.account);
            msalInstance!.setActiveAccount(ssoResult.account);
            setIsLoading(false);

            const tokenResult = await msalInstance!.acquireTokenSilent({
              scopes: [apiScope],
              account: ssoResult.account,
            });
            void fetchProfile(tokenResult.accessToken);
            return;
          }
        } catch {
          // ssoSilent failed — user will see the "Sign in" button
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

  const login = useCallback(() => {
    if (!msalInstance) return;
    void msalInstance.loginRedirect({ scopes: loginScopes });
  }, []);

  const logout = useCallback(() => {
    if (!msalInstance) return;
    setAccount(null);
    setProfile(null);
    if (photoUrl) URL.revokeObjectURL(photoUrl);
    setPhotoUrl(null);
    void msalInstance.logoutRedirect();
  }, [photoUrl]);

  const getApiToken = useCallback(async (): Promise<string | null> => {
    if (!msalInstance || !account) return null;
    try {
      const result = await msalInstance.acquireTokenSilent({
        scopes: [apiScope],
        account,
      });
      return result.accessToken;
    } catch (err) {
      if (err instanceof InteractionRequiredAuthError) {
        // Token expired and can't refresh silently — need interaction
        void msalInstance.acquireTokenRedirect({ scopes: [apiScope] });
        return null;
      }
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
