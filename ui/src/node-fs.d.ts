// The one Node call the tests make (the theme tests read the stylesheets as text); the app
// itself runs in the browser, so Node's full type package isn't installed.
declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf8"): string;
}
