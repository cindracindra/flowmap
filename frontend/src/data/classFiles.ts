import { classFilesRaw } from "virtual:flowmap-data";

// Optional legacy overrides. New graph exports carry sourceFile directly;
// package-derived paths keep older exports usable without any config map.
export const CLASS_FILES: Record<string, string> = classFilesRaw;
