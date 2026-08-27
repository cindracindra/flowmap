import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const config = JSON.parse(
  fs.readFileSync(path.join(projectRoot, 'flowmap.config.json'), 'utf8'),
) as { outputDir: string }
const outputDir = path.resolve(projectRoot, config.outputDir)

function flowmapData(): Plugin {
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

export default defineConfig({
  plugins: [react(), flowmapData()],
})
