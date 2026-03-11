const { getDefaultConfig } = require("expo/metro-config");
const { withNativeWind } = require("nativewind/metro");
const path = require("path");

const config = getDefaultConfig(__dirname);

// Monorepo: watch surf-kit source for hot reload
const surfKitRoot = path.resolve(__dirname, "../../surf-kit");
config.watchFolders = [surfKitRoot];

// Resolve modules from both mobile/node_modules and surf-kit packages
config.resolver.nodeModulesPaths = [
  path.resolve(__dirname, "node_modules"),
  path.resolve(surfKitRoot, "node_modules"),
];

// Deduplicate React — force all imports to use mobile's copy
const mobileModules = path.resolve(__dirname, "node_modules");

config.resolver.extraNodeModules = {
  react: path.resolve(mobileModules, "react"),
  "react-native": path.resolve(mobileModules, "react-native"),
};

// Custom resolver: intercept react imports from surf-kit to prevent duplicate React
const origResolveRequest = config.resolver.resolveRequest;
config.resolver.resolveRequest = (context, moduleName, platform) => {
  // Force react and react-native to always resolve from mobile/node_modules
  if (moduleName === "react" || moduleName.startsWith("react/") ||
      moduleName === "react-native" || moduleName.startsWith("react-native/")) {
    // Resolve the module path within mobile/node_modules
    const resolved = path.resolve(mobileModules, moduleName);
    try {
      // Try to resolve the full file path using Node's resolution
      const filePath = require.resolve(moduleName, { paths: [mobileModules] });
      return { type: "sourceFile", filePath };
    } catch {
      // Fallback to default resolution
    }
  }
  if (origResolveRequest) {
    return origResolveRequest(context, moduleName, platform);
  }
  return context.resolveRequest(context, moduleName, platform);
};

module.exports = withNativeWind(config, { input: "./global.css" });
