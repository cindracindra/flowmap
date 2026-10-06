import fs from 'node:fs'
import path from 'node:path'
import { defineConfig, searchForWorkspaceRoot, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// The analysis output directory is chosen by `./flowmap.sh`, which exports it
// as FLOWMAP_OUTPUT_DIR before starting Vite.
function resolveOutputDir(): string {
  const outputDir = process.env.FLOWMAP_OUTPUT_DIR
  if (!outputDir) {
    throw new Error(
      'FLOWMAP_OUTPUT_DIR is not set. Start the viewer with ./flowmap.sh from the repository root.',
    )
  }
  const resolved = path.resolve(outputDir)
  for (const name of ['topic_cluster.json', 'topic_operations.json']) {
    if (!fs.existsSync(path.join(resolved, name))) {
      throw new Error(`${resolved} has no ${name}. Run the analysis first with ./flowmap.sh.`)
    }
  }
  return resolved
}

function flowmapData(outputDir: string): Plugin {
  const virtualId = 'virtual:flowmap-data'
  const resolvedId = `\0${virtualId}`
  return {
    name: 'flowmap-data',
    resolveId(id) {
      return id === virtualId ? resolvedId : undefined
    },
    load(id) {
      if (id !== resolvedId) return undefined
      const jsonImport = (name: string) => JSON.stringify(path.join(outputDir, `${name}.json`))
      const graphBundlePath = path.join(outputDir, 'graph_bundle.json')
      const graphBundleExport = fs.existsSync(graphBundlePath)
        ? `export { default as graphBundleRaw } from ${JSON.stringify(graphBundlePath)};`
        : 'export const graphBundleRaw = { methodsByEntryId: {}, operationsById: {}, callersByEntryId: {}, operationIdsByMethodEntryId: {} };'
      return [
        `export { default as topicClusterRaw } from ${jsonImport('topic_cluster')};`,
        `export { default as topicOperationsRaw } from ${jsonImport('topic_operations')};`,
        graphBundleExport,
      ].join('\n')
    },
  }
}

export default defineConfig(() => {
  const outputDir = resolveOutputDir()
  return {
    plugins: [react(), flowmapData(outputDir)],
    // The JSON artifacts live outside the frontend folder.
    server: { fs: { allow: [searchForWorkspaceRoot(process.cwd()), outputDir] } },
  }
})
