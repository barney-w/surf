import { Redirect } from "expo-router";

/**
 * Catch-all for unmatched routes — redirects to the home screen.
 *
 * Note: auth callbacks (surf://auth/callback) are handled by expo-auth-session
 * via the Linking API before expo-router processes the URL, so this redirect
 * does not interfere with the OAuth flow.
 */
export default function NotFound() {
  return <Redirect href="/" />;
}
