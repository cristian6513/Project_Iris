import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { build, BuildError } from '../../pagina-iris/scripts/build.mjs';

function buildInto(siteUrl, options = {}) {
    const out = mkdtempSync(join(tmpdir(), 'iris-site-'));
    const result = build({ siteUrl, out, quiet: true, ...options });
    return { out, site: result.siteOut, info: result.info };
}

function readSite(site, path) {
    return readFileSync(join(site, path), 'utf8');
}

test('production build fingerprints assets, absolutizes SEO and keeps every reference resolvable', (t) => {
    const { out, site, info } = buildInto('https://iris.example.test');
    t.after(() => rmSync(out, { recursive: true, force: true }));

    assert.equal(info.siteUrl, 'https://iris.example.test/');
    const index = readSite(site, 'index.html');
    assert.match(index, /<link rel="canonical" href="https:\/\/iris\.example\.test\/">/);
    assert.match(index, /<meta property="og:image" content="https:\/\/iris\.example\.test\/assets\/og-image\.[0-9a-f]{10}\.jpg">/);
    assert.match(index, /<link rel="stylesheet" href="css\/iris-site\.[0-9a-f]{10}\.css">/);
    assert.match(index, /<script src="js\/iris-site\.[0-9a-f]{10}\.js" defer><\/script>/);
    assert.doesNotMatch(index, /\?v=|fonts\.googleapis|\sstyle="/);

    for (const dir of ['css', 'js', 'assets', 'fonts']) {
        for (const name of readdirSync(join(site, dir))) {
            if (/\.(gz|br)$/.test(name) || /^LICENSE/.test(name)) continue;
            assert.match(name, /\.[0-9a-f]{10}\.[a-z0-9]+$/, `${dir}/${name} debería llevar hash`);
        }
    }

    assert.equal(readSite(site, 'robots.txt'), 'User-agent: *\nAllow: /\n\nSitemap: https://iris.example.test/sitemap.xml\n');
    assert.match(readSite(site, 'sitemap.xml'), /<loc>https:\/\/iris\.example\.test\/<\/loc>/);
    assert.match(readSite(site, '404.html'), /<base href="\/">/);
    assert.ok(existsSync(join(site, 'index.html.br')) && existsSync(join(site, 'index.html.gz')));
    assert.ok(!existsSync(join(site, 'scripts')) && !existsSync(join(site, 'site.config.json')), 'no se publican scripts ni config');
});

test('nginx config is rendered for the domain and security headers forbid inline code', (t) => {
    const { out } = buildInto('https://iris-os.example');
    t.after(() => rmSync(out, { recursive: true, force: true }));

    const conf = readFileSync(join(out, 'nginx', 'iris-os.conf'), 'utf8');
    assert.match(conf, /server_name iris-os\.example www\.iris-os\.example;/);
    assert.match(conf, /return 301 https:\/\/iris-os\.example\$request_uri;/);
    assert.match(conf, /root \/var\/www\/iris-os\/current;/);
    assert.doesNotMatch(conf, /\{\{[A-Z_]+\}\}/);
    // A types block replaces mime.types instead of extending it: outside one location, HTML would be octet-stream.
    assert.deepEqual(conf.match(/^\s*types\s*\{[^}]*\}/gm).map((block) => block.trim()), ['types { }']);
    assert.match(conf, /location = \/site\.webmanifest \{\s*types \{ \}\s*default_type application\/manifest\+json;/);
    // HTML never gets a 304: it carries no Content-Type, so a page once cached as octet-stream kept downloading.
    const html = conf.slice(conf.indexOf('location / {'), conf.indexOf('error_page'));
    const notFound = conf.slice(conf.indexOf('location = /404.html {'));
    for (const block of [html, notFound]) {
        assert.match(block, /^\s*etag off;$/m);
        assert.match(block, /^\s*if_modified_since off;$/m);
    }

    const headers = readFileSync(join(out, 'nginx', 'iris-os-security-headers.conf'), 'utf8');
    const csp = headers.match(/Content-Security-Policy "([^"]+)"/)[1];
    assert.doesNotMatch(csp, /unsafe-inline|unsafe-eval/);
    assert.match(csp, /frame-ancestors 'none'/);
    assert.match(headers, /Strict-Transport-Security/);
});

test('build refuses to run without an https site URL', () => {
    const saved = process.env.IRIS_SITE_URL;
    delete process.env.IRIS_SITE_URL;
    try {
        assert.throws(() => build({ siteUrl: 'http://iris.example.test/', quiet: true, out: join(tmpdir(), 'iris-site-never') }), BuildError);
    } finally {
        if (saved !== undefined) process.env.IRIS_SITE_URL = saved;
    }
});

function structuredData(index) {
    return JSON.parse(index.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/)[1]);
}

test('structured data describes the app, the site and the organization with absolute ids and no invented claims', (t) => {
    const { out, site } = buildInto('https://iris.example.test', { social: {} })  // sin perfiles, sea cual sea la config;
    t.after(() => rmSync(out, { recursive: true, force: true }));

    const index = readSite(site, 'index.html');
    const graph = Object.fromEntries(structuredData(index)['@graph'].map((node) => [node['@type'], node]));
    assert.equal(graph.Organization['@id'], 'https://iris.example.test/#organization');
    assert.match(graph.Organization.logo, /^https:\/\/iris\.example\.test\/assets\/icon-512\.[0-9a-f]{10}\.png$/);
    assert.deepEqual(graph.Organization.sameAs, []);
    assert.equal(graph.WebSite.publisher['@id'], graph.Organization['@id']);
    assert.equal(graph.SoftwareApplication.name, 'IRIS OS');
    assert.equal(graph.SoftwareApplication.offers.price, '0');
    assert.equal(graph.SoftwareApplication.publisher['@id'], graph.Organization['@id']);
    assert.ok(!('aggregateRating' in graph.SoftwareApplication) && !('review' in graph.SoftwareApplication), 'no ratings that do not exist');
    assert.doesNotMatch(index, /social:links|<h4>Síguenos|<meta name="twitter:site"/, 'without profiles nothing is shown');
});

test('social profiles from the config become links, sameAs and twitter:site', (t) => {
    const out = mkdtempSync(join(tmpdir(), 'iris-site-'));
    t.after(() => rmSync(out, { recursive: true, force: true }));
    const social = {
        instagram: 'https://www.instagram.com/iris.os/', tiktok: 'https://www.tiktok.com/@iris.os',
        youtube: 'https://www.youtube.com/@IrisOS', x: 'https://x.com/iris_os',
    };
    const result = build({ siteUrl: 'https://iris.example.test', out, quiet: true, social });
    const index = readSite(result.siteOut, 'index.html');

    const organization = structuredData(index)['@graph'].find((node) => node['@type'] === 'Organization');
    assert.deepEqual(organization.sameAs, Object.values(social));
    assert.match(index, /<meta name="twitter:site" content="@iris_os">/);
    assert.match(index, /<h4>Síguenos<\/h4>/);
    for (const [label, url] of [['Instagram', social.instagram], ['TikTok', social.tiktok], ['YouTube', social.youtube], ['X', social.x]]) {
        assert.ok(index.includes(`<a class="btn" href="${url}" rel="me noopener" target="_blank">${label}</a>`), label);
    }
    assert.deepEqual(result.info.social, social);
});

test('a social URL that is not a profile of that network is refused', () => {
    for (const social of [
        { instagram: 'http://www.instagram.com/iris.os/' },
        { instagram: 'https://www.instagram.com/iris.os/?igsh=abc' },
        { tiktok: 'https://www.tiktok.com/iris.os' },
        { youtube: 'https://youtube.example/@IrisOS' },
        { x: 'https://x.com/a_handle_that_is_far_too_long' },
        { facebook: 'https://facebook.com/irisos' },
    ]) {
        assert.throws(() => build({ siteUrl: 'https://iris.example.test', quiet: true, out: join(tmpdir(), 'iris-site-never'), social }),
            BuildError, JSON.stringify(social));
    }
});

test('the Ko-fi page from the config links the Donar card; without it the card stays «Próximamente»', (t) => {
    const donar = /<h3>Donar<\/h3><p>[^<]*<\/p>(<[^\n]*?)<\/div>/;
    const linked = buildInto('https://iris.example.test', { support: { kofi: 'https://ko-fi.com/iris_os' } });
    const unlinked = buildInto('https://iris.example.test', { support: {} });
    t.after(() => {
        rmSync(linked.out, { recursive: true, force: true });
        rmSync(unlinked.out, { recursive: true, force: true });
    });

    assert.equal(readSite(linked.site, 'index.html').match(donar)[1],
        '<a class="feat-link" href="https://ko-fi.com/iris_os" rel="noopener" target="_blank">'
        + 'Apoyar en Ko-fi <svg class="ico"><use href="#i-arrow"/></svg></a>');
    assert.deepEqual(linked.info.support, { kofi: 'https://ko-fi.com/iris_os' });
    assert.equal(readSite(unlinked.site, 'index.html').match(donar)[1], '<span class="soon" data-support="kofi">Próximamente</span>');
    assert.deepEqual(unlinked.info.support, {});
});

test('a support URL that is not a Ko-fi page is refused', () => {
    for (const support of [
        { kofi: 'http://ko-fi.com/iris_os' },
        { kofi: 'https://ko-fi.com/iris_os?ref=x' },
        { kofi: 'https://ko-fi.example/iris_os' },
        { kofi: 'https://ko-fi.com/iris_os/shop' },
        { kofi: 'not a url' },
        { patreon: 'https://www.patreon.com/irisos' },
    ]) {
        assert.throws(() => build({ siteUrl: 'https://iris.example.test', quiet: true, out: join(tmpdir(), 'iris-site-never'), support }),
            BuildError, JSON.stringify(support));
    }
});

test('every version the page shows is the same one', () => {
    const index = readFileSync(new URL('../../pagina-iris/index.html', import.meta.url), 'utf8');
    const shown = [
        index.match(/"softwareVersion": "([^"]+)"/)[1],
        index.match(/Beta en desarrollo · v([0-9.]+)</)[1],
        index.match(/La beta ([0-9.]+) está en pruebas/)[1],
        index.match(/<b>([0-9.]+)<\/b><span>versión beta actual/)[1],
    ];
    assert.equal(new Set(shown).size, 1, `versiones distintas en la página: ${shown.join(', ')}`);
});
