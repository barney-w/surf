import { Slot } from "expo-router";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import { ThemeProvider } from "@surf-kit/theme";
import "../global.css";

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <ThemeProvider colorMode="brand">
        <StatusBar style="light" />
        <Slot />
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
