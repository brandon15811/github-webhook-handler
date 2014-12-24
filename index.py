#!/usr/bin/env python
import io
import os
import re
import sys
import json
import subprocess
import requests
import ipaddress
from hmac import new as hmac
from flask import Flask, request, abort
from werkzeug.contrib.fixers import ProxyFix

app = Flask(__name__)
app.debug = os.environ.get('DEBUG') == 'true'

# The repos.json file should be readable by the user running the Flask app,
# and the absolute path should be given by this environment variable.
REPOS_JSON_PATH = os.environ['FLASK_GITHUB_WEBHOOK_REPOS_JSON']


@app.route("/", methods=['GET', 'POST'])
def index():

    if request.method == 'GET':
        return 'OK'

    elif request.method == 'POST':
        # Store the IP address blocks that github uses for hook requests.
        hook_blocks = requests.get('https://api.github.com/meta').json()['hooks']

        # Check if the POST request if from github.com
        for block in hook_blocks:
            ip = ipaddress.ip_address(u'%s' % request.remote_addr)
            if ipaddress.ip_address(ip) in ipaddress.ip_network(block):
                break #the remote_addr is within the network range of github
        else:
            abort(403)

        event = request.headers.get('X-GitHub-Event')

        if event == "ping":
            return json.dumps({'msg': 'Hi!'})
        if event != "push" and event != "release":
            return json.dumps({'msg': "wrong event type"})

        repos = json.loads(io.open(REPOS_JSON_PATH, 'r').read())

        payload = json.loads(request.data)
        try:
            repo_meta = {
                'name': payload['repository']['name'],
                'owner': payload['repository']['owner']['name'],
            }
        except KeyError:
            repo_meta = {
                'name': payload['repository']['name'],
                'owner': payload['repository']['owner']['login'],
            }

        # Try to match on branch as configured in repos.json
        try:
            match = re.match(r"refs/heads/(?P<branch>.*)", payload['ref'])
            print match
        except KeyError:
            match = re.match(r"(?P<branch>.*)", payload['release']['target_commitish'])
        if match:
            repo_meta['branch'] = match.groupdict()['branch']
            repo = repos.get('{owner}/{name}/branch:{branch}'.format(**repo_meta), None)

            # Fallback to plain owner/name lookup
            if not repo:
               repo = repos.get('{owner}/{name}'.format(**repo_meta), None)
        else:
            return json.dumps({'msg': 'No branch match'})

        if repo and repo.get('path', None):
            # Check if POST request signature is valid
            key = repos.get('key', None)
            if key:
                signature = request.headers.get('X-Hub-Signature').split('=')[1]
                mac = hmac(key, msg=request.data, digestmod=sha1)
                if mac.hexdigest() != signature:
                    abort(403)

            actions = repo.get('actions', None)
            if actions:
                if not actions.get(event, None):
                    return json.dumps({'msg': "no handler registered for event type"})
                if event == 'release':
                    os.environ['GIT_TAG'] = payload['release']['tag_name']
                os.environ['REPO_OWNER'] = repo_meta['owner']
                os.environ['REPO_NAME'] = repo_meta['name']
                for action in actions[event]:
                    subp = subprocess.Popen(action,
                             cwd=repo['path'],
                             env=os.environ)
                    subp.wait()
        return 'OK'

if __name__ == "__main__":
    try:
        port_number = int(sys.argv[1])
    except:
        port_number = 80
    if os.environ.get('USE_PROXYFIX', None) == 'true':
        from werkzeug.contrib.fixers import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app)
    app.run(host='0.0.0.0', port=port_number)
