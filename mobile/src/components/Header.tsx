import { View, Text, Pressable, ActionSheetIOS, Platform, Alert } from "react-native";
import { useAuth } from "../auth/AuthProvider";

interface HeaderProps {
  hasMessages: boolean;
  onNewChat: () => void;
  colorMode: "brand" | "light";
  onToggleColorMode: () => void;
}

/** Pencil icon — simple Unicode character styled to match */
function PencilIcon({ color }: { color: string }) {
  return <Text style={{ fontSize: 14, color, lineHeight: 16 }}>✎</Text>;
}

/** Sun icon — Unicode character */
function SunIcon({ color }: { color: string }) {
  return <Text style={{ fontSize: 18, color, lineHeight: 20 }}>☀︎</Text>;
}

/** Moon icon — Unicode character */
function MoonIcon({ color }: { color: string }) {
  return <Text style={{ fontSize: 16, color, lineHeight: 20 }}>☽</Text>;
}

export function Header({ hasMessages, onNewChat, colorMode, onToggleColorMode }: HeaderProps) {
  const { isAuthenticated, isLoading, profile, login, logout } = useAuth();
  const authConfigured = !!process.env.EXPO_PUBLIC_ENTRA_CLIENT_ID;

  const initials = (profile?.givenName ?? profile?.displayName ?? "?")
    .charAt(0)
    .toUpperCase();

  const showUserMenu = () => {
    if (Platform.OS === "ios") {
      ActionSheetIOS.showActionSheetWithOptions(
        {
          options: [profile?.displayName ?? "User", "Sign out", "Cancel"],
          destructiveButtonIndex: 1,
          cancelButtonIndex: 2,
        },
        (index) => { if (index === 1) void logout(); },
      );
    } else {
      Alert.alert(
        profile?.displayName ?? "User",
        profile?.department ?? undefined,
        [
          { text: "Sign out", style: "destructive", onPress: () => void logout() },
          { text: "Cancel", style: "cancel" },
        ],
      );
    }
  };

  const iconColor = colorMode === "brand" ? "rgba(255,255,255,0.6)" : "rgba(0,0,0,0.5)";

  return (
    <View className="flex-row items-center gap-3 px-5 py-3 border-b border-border shrink-0">
      {/* Brand */}
      <Text className="font-display text-lg font-semibold text-text-primary tracking-tight flex-1">
        Surf
      </Text>

      {/* New Chat */}
      <Pressable
        onPress={onNewChat}
        disabled={!hasMessages}
        className={`flex-row items-center gap-1.5 px-3 py-1.5 rounded-md ${
          hasMessages
            ? "border border-accent/40"
            : "opacity-40"
        }`}
      >
        <PencilIcon color={hasMessages ? "#38bdf8" : iconColor} />
        <Text className={`text-sm font-medium ${hasMessages ? "text-accent" : "text-text-muted"}`}>
          New chat
        </Text>
      </Pressable>

      {/* Theme Toggle */}
      <Pressable
        onPress={onToggleColorMode}
        className="p-1.5 rounded-md"
        accessibilityLabel={`Switch to ${colorMode === "brand" ? "light" : "brand"} theme`}
      >
        {colorMode === "brand" ? (
          <SunIcon color={iconColor} />
        ) : (
          <MoonIcon color={iconColor} />
        )}
      </Pressable>

      {/* Divider */}
      <View className="w-px h-5 bg-border mx-3" />

      {/* User Avatar / Sign In */}
      {isAuthenticated && profile ? (
        <View className="flex-row items-center gap-2">
          <Text className="text-sm text-text-secondary" numberOfLines={1}>
            {profile.givenName ?? profile.displayName}
          </Text>
          <Pressable
            onPress={showUserMenu}
            className="w-8 h-8 rounded-full border border-border-strong items-center justify-center bg-surface"
          >
            <Text className="text-accent text-sm font-semibold">{initials}</Text>
          </Pressable>
        </View>
      ) : authConfigured && !isLoading ? (
        <Pressable
          onPress={login}
          className="border border-border-strong px-3 py-1 rounded-md"
        >
          <Text className="text-sm text-text-secondary">Sign in</Text>
        </Pressable>
      ) : null}
    </View>
  );
}
