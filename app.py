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
    print(f"[WEBHOOK] Received payload: {data}", flush=True)
    for entry in data.get('entry', []):
        for change in entry.get('changes', []):
            print(f"[WEBHOOK] field={change.get('field')} value={change.get('value')}", flush=True)
            if change.get('field') == 'leadgen':
                lead_id = change['value']['leadgen_id']
                print(f"[WEBHOOK] Processing lead_id={lead_id}", flush=True)
                lead_data = get_lead_data(lead_id)
                print(f"[WEBHOOK] Lead data: {lead_data}", flush=True)
                message = format_sms(lead_data)
                send_sms(message)
                print(f"[WEBHOOK] SMS sent successfully", flush=True)
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
        name = field.get('name', '').replace('_', ' ').title()
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

@app.route('/', methods=['GET'])
def health():
    return 'Meta Lead SMS Server is running!', 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
