"""Official search discovery and bounded, DNS-pinned static HTML reads."""
import asyncio
import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
import time
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, urljoin, quote

from bs4 import BeautifulSoup


class WebError(ValueError):
    pass


def canonical_url(url):
    if not isinstance(url,str) or len(url)>4096 or any(ord(c)<33 for c in url) or '\\' in url:
        raise WebError('invalid_url')
    try:
        p=urlsplit(url)
        if p.scheme not in {'http','https'} or not p.hostname or p.username is not None or p.password is not None:
            raise WebError('invalid_url')
        port=p.port or (443 if p.scheme=='https' else 80)
        if port != (443 if p.scheme=='https' else 80):
            raise WebError('nonstandard_port_denied')
        host=p.hostname.encode('idna').decode('ascii').lower().rstrip('.')
        if '%' in host or host=='localhost' or host.endswith(('.localhost','.local','.internal')) or host=='metadata.google.internal':
            raise WebError('private_host_denied')
        display='['+host+']' if ':' in host else host
        return urlunsplit((p.scheme,display,p.path or '/',p.query,''))
    except (UnicodeError,ValueError) as exc:
        if isinstance(exc,WebError): raise
        raise WebError('invalid_url') from None


def public_addresses(url, resolver=None):
    p=urlsplit(canonical_url(url))
    records=(resolver or socket.getaddrinfo)(p.hostname,p.port or (443 if p.scheme=='https' else 80),
                                             type=socket.SOCK_STREAM)
    addresses=list(dict.fromkeys(r[4][0] for r in records))
    if not addresses:
        raise WebError('dns_empty')
    for address in addresses:
        ip=ipaddress.ip_address(address)
        if (not ip.is_global or ip.is_multicast or ip.is_unspecified or
                (isinstance(ip,ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo))):
            raise WebError('non_public_address_denied')
    return addresses


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, secure, timeout):
        super().__init__(host,port,timeout=timeout)
        self.address,self.secure=address,secure

    def connect(self):
        # Connect to the validated address without another hostname resolution.
        family=socket.AF_INET6 if ':' in self.address else socket.AF_INET
        sock=socket.socket(family,socket.SOCK_STREAM)
        self.wire_socket=sock
        try:
            sock.settimeout(self.timeout)
            sock.connect((self.address,self.port))
            if self.secure:
                self.sock=ssl.create_default_context().wrap_socket(sock,server_hostname=self.host,do_handshake_on_connect=False)
                self.wire_socket=self.sock
                self.sock.do_handshake()
            else:self.sock=sock
        except BaseException:
            sock.close()
            raise


def read_http(url, address, deadline, max_bytes, method='GET', body=None, headers=None):
    p=urlsplit(url)
    remaining=deadline-time.monotonic()
    if remaining<=0: raise TimeoutError('web_deadline')
    connection=PinnedConnection(p.hostname,443 if p.scheme=='https' else 80,address,p.scheme=='https',remaining)
    def abort():
        # socket timeouts alone reset on each header read. A hard wall timer
        # interrupts TLS/header trickle and HTTP/1.0 response-owned sockets too.
        sock=getattr(connection,'wire_socket',None)
        if sock:
            try:sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            try:sock.close()
            except OSError:pass
    timer=threading.Timer(remaining,abort);timer.daemon=True;timer.start()
    try:
        target=quote(p.path or '/',safe="/%:@!$&'()*+,;=-._~")+('?' + quote(p.query,safe="%/?@!$&'()*+,;=:-._~") if p.query else '')
        connection.request(method,target,body=body,headers={'User-Agent':'KnowledgeAgent/1.0 (public static evidence reader)',
            'Accept-Encoding':'identity',**(headers or {})})
        response=connection.getresponse()
        metadata={k.lower():v for k,v in response.getheaders()}
        if response.status in {301,302,303,307,308}:
            return response.status,metadata,b''
        if response.status!=200:
            raise WebError('http_'+str(response.status))
        if metadata.get('content-encoding','identity').lower() not in {'identity',''}:
            raise WebError('compressed_response_not_supported')
        expected = None
        if 'content-length' in metadata:
            try: expected=int(metadata['content-length'])
            except ValueError: raise WebError('invalid_content_length') from None
            if expected<0: raise WebError('invalid_content_length')
        if expected is not None and expected>max_bytes:
            raise WebError('response_size_limit')
        parts=[]; size=0
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0: raise TimeoutError('web_deadline')
            if connection.sock: connection.sock.settimeout(remaining)
            # read1 returns available data; enforce a wall deadline against trickle responses.
            part=response.read1(min(65536,max_bytes+1-size))
            if not part: break
            size+=len(part)
            if size>max_bytes: raise WebError('response_size_limit')
            parts.append(part)
        if time.monotonic()>=deadline: raise TimeoutError('web_deadline')
        if expected is not None and size!=expected: raise WebError('incomplete_response')
        return response.status,metadata,b''.join(parts)
    finally:
        timer.cancel()
        connection.close()


async def pinned_request(url, deadline, max_bytes, **kwargs):
    addresses=await asyncio.wait_for(asyncio.to_thread(public_addresses,url),max(.001,deadline-time.monotonic()))
    # Drain bounded socket IO on cancellation before releasing the request scope.
    task=asyncio.create_task(asyncio.to_thread(read_http,url,addresses[0],deadline,max_bytes,**kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task,return_exceptions=True)
        raise


class TavilySearch:
    def __init__(self, settings):
        self.s=settings

    async def search(self, question):
        if self.s.app_mode=='offline': raise WebError('network_disabled_offline')
        if self.s.search_provider!='tavily' or not self.s.search_api_key.get_secret_value():
            raise WebError('search_not_configured')
        payload={'query':question,'search_depth':'basic','max_results':self.s.search_max_results,
                 'include_answer':False,'include_raw_content':False,'auto_parameters':False,
                 'include_published_date':True,'include_usage':True}
        deadline=time.monotonic()+self.s.search_timeout
        # One paid attempt. 429 and budget errors are recorded, never retry-spammed.
        status,headers,raw=await pinned_request('https://api.tavily.com/search',deadline,1000000,
            method='POST',body=json.dumps(payload).encode(),
            headers={'Authorization':'Bearer '+self.s.search_api_key.get_secret_value(),'Content-Type':'application/json'})
        if status!=200: raise WebError('search_redirect_denied')
        data=json.loads(raw)
        if not isinstance(data.get('results'),list): raise WebError('search_response_contract')
        results=[]
        for row in data['results'][:self.s.search_max_results]:
            if not isinstance(row,dict) or not isinstance(row.get('url'),str): continue
            try: url=canonical_url(row['url'])
            except WebError: continue
            results.append({'url':url,'title':str(row.get('title') or '')[:500],
                'snippet':str(row.get('content') or '')[:3000], 'published_at':row.get('published_date'),
                'read_status':'discovered_not_read'})
        return {'results':results,'usage':data.get('usage'),'provider':'tavily'}


class PageReader:
    def __init__(self, settings):
        self.s=settings
        self.semaphore=asyncio.Semaphore(settings.web_concurrency)

    async def read(self, discovered):
        async with self.semaphore:
            if self.s.app_mode=='offline': raise WebError('network_disabled_offline')
            original=canonical_url(discovered['url']); current=original
            deadline=time.monotonic()+self.s.web_timeout
            redirects=[]
            for hop in range(self.s.web_max_redirects+1):
                status,headers,raw=await pinned_request(current,deadline,self.s.web_max_bytes)
                if status in {301,302,303,307,308}:
                    if hop==self.s.web_max_redirects: raise WebError('redirect_limit')
                    target=canonical_url(urljoin(current,headers.get('location','')))
                    if urlsplit(current).scheme=='https' and urlsplit(target).scheme=='http':
                        raise WebError('https_downgrade_denied')
                    if target in redirects or target==current: raise WebError('redirect_loop')
                    redirects.append(current); current=target
                    continue
                content_type=headers.get('content-type','').split(';')[0].strip().lower()
                if content_type not in {'text/html','application/xhtml+xml'}:
                    raise WebError('static_html_only')
                return html_snapshot(raw,current,original,discovered,self.s.web_max_text_chars,redirects)
            raise WebError('redirect_limit')


def html_snapshot(raw,url,original,discovered,max_chars,redirects=()):
    soup=BeautifulSoup(raw,'html.parser')
    title=soup.title.get_text(' ',strip=True)[:500] if soup.title else discovered.get('title','')
    published=None; date_basis=None
    for key in ('article:published_time','datePublished','date','pubdate'):
        tag=soup.find('meta',attrs={'property':key}) or soup.find('meta',attrs={'name':key})
        if tag and tag.get('content'):
            published=str(tag['content'])[:200]; date_basis='page_metadata_unverified';break
    if published is None:
        tag=soup.find('time',attrs={'datetime':True})
        if tag:
            published=str(tag['datetime'])[:200];date_basis='page_time_unverified'
    for tag in soup(['script','style','noscript','iframe','form','nav','footer','header','svg']): tag.decompose()
    root=soup.find('main') or soup.find('article') or soup.body or soup
    text=root.get_text('\n',strip=True)
    if not text.strip(): raise WebError('no_static_body')
    fetched=datetime.now(timezone.utc).isoformat()
    html_hash=hashlib.sha256(raw).hexdigest()
    evidence_id='web:'+hashlib.sha256((url+'|'+fetched+'|'+html_hash).encode()).hexdigest()
    return {'id':evidence_id,'source_type':'web','url':url,'discovered_url':original,'title':title,
        'fetched_at':fetched,'published_at':published,'published_at_basis':date_basis,
        'search_published_at':discovered.get('published_at'),'text':text[:max_chars],
        'read_status':'body_read','truncated':len(text)>max_chars,'body_chars':len(text),
        'html_snapshot':raw.decode(soup.original_encoding or 'utf-8',errors='replace'),
        'html_sha256':html_hash,'body_sha256':hashlib.sha256(text[:max_chars].encode()).hexdigest(),
        'headings':[],'locations':[{'url':url}],'redirects':list(redirects)}
