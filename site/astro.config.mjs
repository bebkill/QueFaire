import { defineConfig } from 'astro/config';

// `site` et `base` sont pilotés par l'environnement pour le déploiement
// GitHub Pages (voir .github/workflows/refresh.yml). En local : racine.
export default defineConfig({
  site: process.env.SITE_URL || 'http://localhost:4321',
  base: process.env.SITE_BASE || '/',
});
