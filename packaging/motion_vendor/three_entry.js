/* How video_studio/motion_templates/vendor/three-motion.min.js was made (three.js 0.187.0, MIT):
     npm pack three@0.187.0  ->  unpack to ./three
     esbuild three_entry.js --bundle --minify --format=iife --global-name=THREE --legal-comments=eof --outfile=three-motion.min.js
   Only the parts the motion templates use are kept (577 KB instead of 2.1 MB).
   The 3D font: node make_typeface.cjs <fontkit dir> video_studio/assets/fonts/Mukta-ExtraBold.ttf mukta_extrabold.typeface.json
   (Mukta, SIL Open Font License 1.1; Latin and Latin-1 outlines only). esbuild and fontkit came from the HyperFrames
   npm install; neither ships with Takebloom. */
export { WebGLRenderer, Scene, PerspectiveCamera, Group, Mesh, MeshStandardMaterial, MeshPhysicalMaterial, MeshBasicMaterial,
  AmbientLight, DirectionalLight, PointLight, HemisphereLight, Color, Vector3, MathUtils,
  BoxGeometry, SphereGeometry, TorusGeometry, TorusKnotGeometry, IcosahedronGeometry, OctahedronGeometry, ConeGeometry,
  CylinderGeometry, ExtrudeGeometry, Shape, SRGBColorSpace, ACESFilmicToneMapping, NoToneMapping } from "./three/build/three.module.js";
export { FontLoader, Font } from "./three/examples/jsm/loaders/FontLoader.js";
export { TextGeometry } from "./three/examples/jsm/geometries/TextGeometry.js";
