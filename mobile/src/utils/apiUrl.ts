import { Platform } from "react-native";

const DEFAULT_PORT = "8090";
const API_PATH = "/api/v1";

/**
 * Resolves the API base URL for the current platform.
 *
 * On Android emulators, `localhost` refers to the emulator's own loopback —
 * not the host machine. We rewrite it to `10.0.2.2` which is the standard
 * Android emulator alias for the host's loopback interface.
 */
export function getApiUrl(): string {
  const envUrl = process.env.EXPO_PUBLIC_API_URL;

  if (envUrl) {
    if (Platform.OS === "android") {
      return envUrl.replace("localhost", "10.0.2.2").replace("127.0.0.1", "10.0.2.2");
    }
    return envUrl;
  }

  // Fallback when no env var is set
  const host = Platform.OS === "android" ? "10.0.2.2" : "localhost";
  return `http://${host}:${DEFAULT_PORT}${API_PATH}`;
}
