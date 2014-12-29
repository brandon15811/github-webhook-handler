#!/usr/bin/env python
#TODO: Use a proper WSGI server
import io
import os
import re
import sys
import json
import subprocess
import requests
import ipaddress
import hmac
from hashlib import sha1
from flask import Flask, request, abort
from werkzeug.contrib.fixers import ProxyFix
from docker import Client
from pymongo import MongoClient

mongodb_host = os.getenv('MONGODB_HOST', None) or os.getenv('MONGO_PORT_27017_TCP_ADDR', None) or 'localhost'
mongodb_port = os.getenv('MONGODB_PORT', None) or os.getenv('MONGO_PORT_27017_TCP_PORT', None) or 27017

client = MongoClient(mongodb_host, int(mongodb_port))
db = client.github_webhook_builder

docker = Client(base_url='unix://var/run/docker.sock')

app = Flask(__name__)
app.debug = os.environ.get('DEBUG').lower() == 'true'

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
            pass
            #TODO: Re-enable me
            #abort(403)

        event = request.headers.get('X-GitHub-Event')

        if event == "ping":
            return json.dumps({'msg': 'Hi!'})
        if event != "push" and event != "release":
            return json.dumps({'msg': "wrong event type"})

        if request.headers.get('Content-Type') != 'application/json':
            return json.dumps({'msg': 'Wrong content type'})


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

        #TODO: Fix crash on tag push
        # Try to match on branch as configured in json config
        try:
            match = re.match(r"refs/heads/(?P<branch>.*)", payload['ref'])
        except KeyError:
            match = re.match(r"(?P<branch>.*)", payload['release']['target_commitish'])
        if match:
            repo_meta['branch'] = match.groupdict()['branch']
            #Change to mongo find()
            repo = db.hooks.find_one({'repo': '{owner}/{name}/branch:{branch}'.format(**repo_meta)}, None)

            # Fallback to plain owner/name lookup
            if not repo:
               repo = db.hooks.find_one({'repo': '{owner}/{name}'.format(**repo_meta)}, None)
        else:
            return json.dumps({'msg': 'No branch match'})

        if repo and repo.get('path', None):
            # Check if POST request signature is valid
            key = repo.get('key', None)
            if key:
                signature = request.headers.get('X-Hub-Signature').split('=')[1]
                if type(key) == unicode:
                    key = key.encode()
                mac = hmac.new(key, msg=request.data, digestmod=sha1)
                if not compare_digest(mac.hexdigest(), signature):
                    abort(403)

            actions = repo.get('actions', None)
            if actions:
                if not actions.get(event, None):
                    return json.dumps({'msg': "no handler registered for event type"})
                env = {}

                apparmor = '';
                if os.environ.get('APPARMOR', '').lower() == 'true':
                    apparmor = '/usr/sbin/aa-exec -p plugin_build -- ';
                command = apparmor + '/tools/build.sh'
                if event == 'release':
                    env['GIT_TAG'] = payload['release']['tag_name']
                    command = apparmor + '/tools/build.sh -r'
                env['REPO_OWNER'] = repo_meta['owner']
                env['REPO_NAME'] = repo_meta['name']
                project_name = None
                if os.environ.get('MONGO_NAME', None):
                    #Get image name from fig project name
                    project_name = os.environ['MONGO_NAME'].split('/')[1].split('_')[0]
                    nginx_container_name = project_name + '_nginx_1'
                    image_name = project_name + '_build'
                else:
                    image_name = 'pdchook_build'
                    nginx_container_name = 'pdchook_nginx_1'
                container = docker.create_container(
                    image=image_name,
                    command=command,
                    environment=env,
                    volumes=['/builds']
                )
                docker.start(container,
                    cap_drop=['all'],
                    volumes_from=[nginx_container_name]
                    #binds={
                    #    '/var/www/pluginbuild':
                    #        {
                    #            'bind': '/builds',
                    #            'ro': False
                    #        }
                    #}
                )
                #TODO: Remove this for production
                if app.debug:
                    docker.wait(container)
                    print docker.logs(container, stdout=True, stderr=True, timestamps=True)
                    #docker.remove_container(container)
                    sys.stdout.flush()
                #TODO: Compile all actions into one script (maybe use docker exec instead?)
#                for action in actions[event]:
#                    subp = subprocess.Popen(action,
#                             cwd=repo['path'],
#                             env=env)
#                    subp.wait()
        else:
            abort(404)
        return 'OK'

#Check if python version is less than 2.7.7
if sys.version_info<(2,7,7):
    #http://blog.turret.io/hmac-in-go-python-ruby-php-and-nodejs/
    def compare_digest(a, b):
	    """
	    ** From Django source **

	    Run a constant time comparison against two strings

	    Returns true if a and b are equal.

	    a and b must both be the same length, or False is
	    returned immediately
	    """
	    if len(a) != len(b):
		    return False

	    result = 0
	    for ch_a, ch_b in zip(a, b):
		    result |= ord(ch_a) ^ ord(ch_b)
	    return result == 0
else:
    compare_digest = hmac.compare_digest

if __name__ == "__main__":
    try:
        port_number = int(sys.argv[1])
    except:
        port_number = 80
    if os.environ.get('USE_PROXYFIX', None) == 'true':
        from werkzeug.contrib.fixers import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app)
    app.run(host='0.0.0.0', port=port_number)
