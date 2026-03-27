import { createContext, useContext } from "react";
import type { ColorMode } from "@surf-kit/theme";

export interface ColorModeContextValue {
  colorMode: ColorMode;
  toggleColorMode: () => void;
}

export const ColorModeContext = createContext<ColorModeContextValue>({
  colorMode: "brand",
  toggleColorMode: () => {},
});

export function useColorMode(): ColorModeContextValue {
  return useContext(ColorModeContext);
}
