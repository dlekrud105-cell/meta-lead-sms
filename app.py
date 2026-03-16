import os
import boto3
import requests
from flask import Flask, request

app = Flask(__name__)

VERIFY_TOKEN      = os.environ.get('META_VERIFY_TOKEN')
META_ACCESS_TOKEN = os.environ.get('META_ACCESS_TOKEN')
AWS_ACCESS_KEY    = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_KEY    = os.environ.get('AWS_SECRET_ACCESS_KEY')
MY_PHONE          = os.environ.get('MY_PHONE_NUMBER')
SENDER_ID         = os.environ.get('SENDER_ID', 'LEADS')


@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode      = request.args.get('hub.mode')
    token     = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    if mode == 'subscribe' and token == VERIFY_TOKEN:
        return challenge, 200
    return 'Forbidden', 403


@app.route('/webhook', methods=['POST'])
def receive_lead():
    data = request.json
    for entry in data.get('entry', []):
        for change in entry.get('changes', []):
            if change.get('field') == 'leadgen':
                lead_id = change['value']['leadgen_id']
                lead_data = get_lead_data(lead_id)
                message = format_sms(lead_data)
                send_sms(message)
    return 'OK', 200


def get_lead_data(lead_id):
    url = f'https://graph.facebook.com/v19.0/{lead_id}'
    params = {'access_token': META_ACCESS_TOKEN, 'fields': 'field_data,created_time,ad_name'}
    response = requests.get(url, params=params)
    return response.json()


def format_sms(lead_data):
    lines = ['New Lead Submitted']
    if 'ad_name' in lead_data:
        lines.append(f'Form: {lead_data["ad_name"]}')
    lines.append('-' * 20)
    for field in lead_data.get('field_data', []):
        name  = field.get('name', '').replace('_', ' ').title()
        value = field.get('values', [''])[0]
        lines.append(f'{name}: {value}')
    return '\n'.join(lines)


def send_sms(message):
    sns = boto3.client('sns', region_name='ap-southeast-2',
                       aws_access_key_id=AWS_ACCESS_KEY,
                       aws_secret_access_key=AWS_SECRET_KEY)
    sns.publish(
        PhoneNumber=MY_PHONE,
        Message=message,
        MessageAttributes={
            'AWS.SNS.SMS.SMSType': {'DataType': 'String', 'StringValue': 'Transactional'},
            'AWS.SNS.SMS.SenderID': {'DataType': 'String', 'StringValue': SENDER_ID}
        }
    )


@app.route('/oauth', methods=['GET'])
def oauth_callback():
        html = """<!DOCTYPE html>
        <html>
        <head><title>Token Setup</title>
        <style>
        body{font-family:Arial,sans-serif;max-width:900px;margin:40px auto;padding:20px}
        code{display:block;background:#f0f0f0;padding:12px;word-break:break-all;font-size:13px;border-radius:4px}
        .page-token{background:#d4edda;border:1px solid #28a745}
        .page-block{border:1px solid #ccc;padding:15px;margin:10px 0;border-radius:6px}
        button{background:#28a745;color:white;border:none;padding:8px 16px;cursor:pointer;border-radius:4px;margin-top:8px}
        </style></head>
        <body>
        <h1>Facebook Token Setup</h1>
        <div id="result"><p>Reading token from URL...</p></div>
        <script>
        function copyText(id){
          var t=document.getElementById(id).textContent;
            navigator.clipboard.writeText(t).then(function(){alert('Copied!');}).catch(function(){
                var ta=document.createElement('textarea');ta.value=t;document.body.appendChild(ta);ta.select();document.execCommand('copy');document.body.removeChild(ta);alert('Copied!');
                  });
                  }
                  var hash=window.location.hash.substring(1);
                  var params=new URLSearchParams(hash);
                  var token=params.get('access_token');
                  if(token){
                    document.getElementById('result').innerHTML='<h2>User Token:</h2><code id="ut">'+token+'</code><button onclick="copyText(\'ut\')">Copy</button><hr><p>Fetching pages...</p>';
                      fetch('https://graph.facebook.com/v19.0/me/accounts?access_token='+encodeURIComponent(token))
                          .then(function(r){return r.json();})
                              .then(function(data){
                                    var html='<h2>User Token:</h2><code id="ut">'+token+'</code><button onclick="copyText(\'ut\')">Copy User Token</button><hr>';
                                          if(data.data&&data.data.length>0){
                                                  html+='<h2>Page Tokens ('+data.data.length+' pages):</h2>';
                                                          data.data.forEach(function(p,i){
                                                                    var tid='pt'+i;
                                                                              html+='<div class="page-block"><b>'+p.name+'</b> (ID:'+p.id+')<br>Page Token:<br><code class="page-token" id="'+tid+'">'+p.access_token+'</code><button onclick="copyText(\''+tid+'\')">Copy Page Token</button></div>';
                                                                                      });
                                                                                            }else{
                                                                                                    html+='<p>No pages found: <pre>'+JSON.stringify(data,null,2)+'</pre></p>';
                                                                                                          }
                                                                                                                document.getElementById('result').innerHTML=html;
                                                                                                                    });
                                                                                                                    }else{
                                                                                                                      document.getElementById('result').innerHTML='<p style="color:red">Error: '+(params.get('error_description')||'No token in URL')+'</p>';
                                                                                                                      }
                                                                                                                      </script>
                                                                                                                      </body></html>"""
        return html



@app.route('/', methods=['GET'])
def health():
    return 'Meta Lead SMS Server is running!', 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
