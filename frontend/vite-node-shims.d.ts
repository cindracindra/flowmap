declare module "node:fs" {
  const fs: {
    readFileSync(path: string, encoding: "utf8"): string;
    existsSync(path: string): boolean;
  };
  export default fs;
}

declare module "node:path" {
  const path: {
    dirname(path: string): string;
    join(...paths: string[]): string;
    resolve(...paths: string[]): string;
  };
  export default path;
}

declare module "node:url" {
  export function fileURLToPath(url: string): string;
}

interface ImportMeta {
  readonly url: string;
}
