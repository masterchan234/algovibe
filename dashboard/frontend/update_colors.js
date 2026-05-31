const fs = require('fs');
const path = require('path');

const directory = '/home/chan/projects/algovibe/dashboard/frontend/src';

const replacements = {
  '#0d1117': 'var(--bg-deep)',
  '#161b22': 'var(--bg-surface)',
  '#21262d': 'var(--border-light)',
  '#30363d': 'var(--border-focus)',
  '#e6edf3': 'var(--text-primary)',
  '#8b949e': 'var(--text-secondary)'
};

function walk(dir) {
  let results = [];
  const list = fs.readdirSync(dir);
  list.forEach(function(file) {
    file = path.join(dir, file);
    const stat = fs.statSync(file);
    if (stat && stat.isDirectory()) { 
      results = results.concat(walk(file));
    } else if (file.endsWith('.vue')) {
      results.push(file);
    }
  });
  return results;
}

const files = walk(directory);
for (const file of files) {
  let content = fs.readFileSync(file, 'utf8');
  let changed = false;
  
  for (const [key, value] of Object.entries(replacements)) {
    // Regex for case-insensitive global replace
    const regex = new RegExp(key, 'gi');
    if (regex.test(content)) {
      content = content.replace(regex, value);
      changed = true;
    }
  }
  
  if (changed) {
    fs.writeFileSync(file, content);
    console.log(`Updated ${file}`);
  }
}
