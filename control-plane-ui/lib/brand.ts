/** RaiCentral logo identity — navy wordmark + teal accent (see issue ZTEC-134). */
export const BRAND = {
  navy: "#122132",
  teal: "#008467",
  tealBright: "#009e7a",
  tealDeep: "#006b52",
} as const;

export type BrandColorKey = keyof typeof BRAND;