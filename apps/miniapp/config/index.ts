import { defineConfig, type UserConfigExport } from "@tarojs/cli";
import path from "path";

export default defineConfig(async () => {
  const apiBaseUrl = process.env.TARO_APP_API_BASE_URL || "";
  const nodeEnv = process.env.NODE_ENV || "production";
  const taroEnv = process.env.TARO_ENV || "";
  const config: UserConfigExport = {
    projectName: "GreenVideo AI",
    date: "2026-06-27",
    designWidth: 375,
    deviceRatio: {
      375: 2,
      640: 1.17,
      750: 1,
      828: 0.905
    },
    sourceRoot: "src",
    outputRoot: "dist",
    alias: {
      "@": path.resolve(__dirname, "..", "src")
    },
    plugins: [],
    defineConstants: {
      "process.env.TARO_APP_API_BASE_URL": JSON.stringify(apiBaseUrl),
      "process.env.NODE_ENV": JSON.stringify(nodeEnv),
      "process.env.TARO_ENV": JSON.stringify(taroEnv),
      process: JSON.stringify({
        env: {
          TARO_APP_API_BASE_URL: apiBaseUrl,
          NODE_ENV: nodeEnv,
          TARO_ENV: taroEnv
        }
      })
    },
    copy: {
      patterns: [],
      options: {}
    },
    framework: "react",
    compiler: {
      type: "webpack5",
      prebundle: {
        enable: false
      }
    },
    cache: {
      enable: false
    },
    mini: {
      postcss: {
        pxtransform: {
          enable: true,
          config: {}
        },
        cssModules: {
          enable: false,
          config: {
            namingPattern: "module",
            generateScopedName: "[name]__[local]___[hash:base64:5]"
          }
        }
      }
    },
    h5: {
      publicPath: "/",
      staticDirectory: "static",
      router: {
        mode: "hash"
      }
    }
  };
  return config;
});
