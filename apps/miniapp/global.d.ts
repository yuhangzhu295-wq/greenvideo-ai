declare module "*.scss";
declare module "*.png";
declare module "*.jpg";
declare module "*.svg";

declare namespace NodeJS {
  interface ProcessEnv {
    TARO_APP_API_BASE_URL?: string;
  }
}
