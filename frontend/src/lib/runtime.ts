/** NEXT_PUBLIC_* はbuild時に公開bundleへ埋め込まれる。秘密値は置かない。 */
export const PUBLIC_READ_ONLY = process.env.NEXT_PUBLIC_READ_ONLY === "true";
/** 公開syntheticデモ上で、期限付き投資枠の入力だけを見せる。 */
export const PUBLIC_DEMO_WRITES =
  process.env.NEXT_PUBLIC_PUBLIC_DEMO_WRITE_ENABLED === "true";
