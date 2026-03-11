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

// Ensure React is deduplicated (use mobile's copy)
config.resolver.extraNodeModules = {
  react: path.resolve(__dirname, "node_modules/react"),
  "react-native": path.resolve(__dirname, "node_modules/react-native"),
};

module.exports = withNativeWind(config, { input: "./global.css" });
