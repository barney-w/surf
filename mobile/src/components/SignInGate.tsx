import { View, Text, Pressable } from "react-native";
import { useAuth } from "../auth/AuthProvider";

/**
 * A translucent sign-in prompt card shown when authentication is required
 * but the user has not yet signed in. Matches the web glass-panel design.
 */
export function SignInGate() {
  const { login, error } = useAuth();

  return (
    <View className="w-full max-w-[420px] items-center rounded-3xl border border-[rgba(225,185,137,0.15)] bg-[rgba(10,54,66,0.85)] px-8 py-10">
      {/* Lock icon in a cyan-tinted circle */}
      <View className="mb-5 h-16 w-16 items-center justify-center rounded-full bg-[rgba(56,189,248,0.12)]">
        <Text className="text-3xl">🔒</Text>
      </View>

      <Text className="mb-2 text-center text-xl font-semibold text-white">
        Sign in to start chatting
      </Text>

      <Text className="mb-8 text-center text-sm leading-5 text-[rgba(255,255,255,0.6)]">
        Authentication is required to access this service. Sign in with your
        organisation account to continue.
      </Text>

      {error && (
        <Text className="mb-4 text-center text-sm text-red-400">
          {error}
        </Text>
      )}

      <Pressable
        onPress={login}
        className="rounded-xl bg-[#38bdf8] px-10 py-3 active:opacity-80"
      >
        <Text className="text-base font-semibold text-[#0a3642]">Sign in</Text>
      </Pressable>
    </View>
  );
}
