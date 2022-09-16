#!/usr/bin/env python3

from typing import List, Tuple
import requests
import itertools
import os
import time
import sys
from datetime import datetime

# setting up the parameters
KEY=os.getenv('OPENWEATHERMAPAPI')
NTFY=os.getenv('NTFY_WEATHER') #  got with: `date +%F | sha512sum | cut -c 1-12`
LAT, LON = 53.233290407599135, 6.544686326584316 # Groningen
if KEY is None:
    raise ValueError('No API key provided (variable OPENWEATHERMAPAPI)')
if NTFY is None:
    raise ValueError('No NTFY key provided (variable NTFY_WEATHER)')


class Loop:
    def __init__(self, 
            next_hour_checks_at: List[str] = ["8:00", "13:00", "17:00", "20:00"],
            report_checks_at: List[str] = ["7:00", "13:00"], 
            ):
        self.start = datetime.now()

        rv = []
        for stamp in next_hour_checks_at:
            hh, mm = map(int, stamp.split(':'))
            assert 0 <= hh <= 23, hh
            assert 0 <= mm <= 60, mm
            rv.append((hh, mm))
        self.next_hour_checks_at = rv

        rv = []
        for stamp in report_checks_at:
            hh, mm = map(int, stamp.split(':'))
            assert 0 <= hh <= 23, hh
            assert 0 <= mm <= 60, mm
            rv.append((hh, mm))
        self.report_checks_at = rv
    
    def next_hour_checks_string(self) -> str:
        rv = []
        for hh, mm in self.next_hour_checks_at:
            rv.append(f'{hh:02}:{mm:02}')
        return ', '.join(rv)

    def report_checks_string(self) -> str:
        rv = []
        for hh, mm in self.report_checks_at:
            rv.append(f'{hh:02}:{mm:02}')
        return ', '.join(rv)
    
    def regular_is_triggered(self):
        now = datetime.now()
        for hh, mm in self.next_hour_checks_at:
            if hh == now.hour and mm == now.minute:
                return True
        return False

    def report_is_triggered(self):
        now = datetime.now()
        for hh, mm in self.report_checks_at:
            if hh == now.hour and mm == now.minute:
                return True
        return False

def simplify_hour(h: dict) -> dict:
    uvi = h.get('uvi')
    temp = h.get('temp') - 273.15
    wind = h.get('wind_speed')
    prob_rain = h.get('pop')
    rain = h.get('rain')

    rv = {}
    rv['UV'] = uvi
    rv['COLD'] = int(temp)
    rv['HOT'] = int(temp)
    rv['WIND'] = int(wind)
    rv['PROB_RAIN'] = int(prob_rain*100)
    if rain and rain.get('1h') and rain.get('1h') > 0.2:
        rv['RAINDROPS'] = rain.get("1h")

    return rv


def condition_pairs_to_string(arr: Tuple[int, bool]) -> str:    
    good_hours = [h for h, condition in arr if condition == True]

    pairs = []
    for a, b in itertools.groupby(enumerate(good_hours), 
            lambda pair: pair[1] - pair[0]):
        b = list(b)
        pairs.append((b[0][1], b[-1][1]))

    rv = []
    for t0, t1 in pairs:
        if t0 == t1:
            rv.append(f'{t0}')
        else:
            rv.append(f'{t0}-{t1}')
    return ', '.join(rv)

class Status:
    def __init__(self, d: dict):
        conditions = {
                'PROB_RAIN':lambda x: x > 50,
                'COLD': lambda x: x < 15,
                'UV': lambda x: x > 2,
                'HOT': lambda x: x > 25,
                }
        emojis = {
                'PROB_RAIN':u"\U0001F327",
                'COLD':u"\U0001F976",
                'UV':u"\uE04A",
                'HOT':u"\U0001F975",
                }
        self.conditions = conditions
        self.emojis = emojis

        self.values = {k:v for k, v in d.items() if k in conditions}
        self.bools = {
                key:test(d[key])
                for key, test in conditions.items()
                if key in d
                }
        self.warnings = {
                key:d.get(key)
                for key, test in self.conditions.items()
                if key in d
                and test(d.get(key))
                }

    def get_long_message(self) -> str:
        emojis = self.emojis
        rv = [f'{emojis.get(k)}: {int(v)}' for k, v in self.warnings.items()] 
        return '\n'.join(rv)
    
    def get_most_important_warning(self) -> str:
        for name, value in self.warnings.items():
            return name
        return ''

    def __bool__(self):
        return not bool(self.warnings)
        
class Weather:
    def __init__(self):
        self.report_warnings = {}
        self.regular_warnings = {}
        self._requesttemplate = f'https://api.openweathermap.org/data/3.0/onecall?lat={LAT}&lon={LON}&appid={KEY}'
        self.is_good = True
        self.warning = ''
        self.message = ''

    def check_next_hour(self, debug = False):
        request = f'{self._requesttemplate}&exclude=minutely,daily,alerts,current'
        reply = requests.get(request)
        if debug:
            return reply
        response = reply.json()['hourly']
        simple_dict = simplify_hour(response[0])
        status = Status(simple_dict)
        if bool(status):
            self.is_good = True
            self.warning = ''
            self.message = ''
        else:
            self.is_good = False
            self.warning = f'next hour: {status.get_most_important_warning()}'
            self.message = status.get_long_message()

    def check_report(self, last_hour: int = 24):
        assert 0 <= last_hour <= 24 # deliberate <= at the end to allow for it to be float('inf')-ish
        now = datetime.now()
        reply = requests.get(f'{self._requesttemplate}&exclude=minutely,daily,alerts,current').json()
        next_day = reply['hourly'][:24]
         
        # gather info about upcoming hours
        hours = []
        for hour_dict in next_day:
            hh = datetime.fromtimestamp(hour_dict['dt']).hour
            if hh < now.hour or hh >= last_hour: # already for tomorrow
                break 
            simple_dict = simplify_hour(hour_dict) 
            status = Status(simple_dict)
            hours.append((hh, status))

        rv = []
        for cond_name in Status({}).conditions:
            emoji = Status({}).emojis[cond_name]
            cond_arr = [
                    (hh, status.bools.get(cond_name, False))
                    for hh, status in hours
                    ]
            if any([pair[1] for pair in cond_arr]):
                range_string = condition_pairs_to_string(cond_arr)
                msg = f'{emoji}: {range_string}'
                rv.append(msg)

        if not rv:
            self.is_good = True
            self.warning = ''
            self.message = 'Good weather!' + ' ' + u"\U0001F917"
        else:
            self.is_good = False
            self.warning = 'prepare for the day'
            self.message = '\n'.join(rv)


class Notifications:
    def __init__(self):
        self.target = f"https://ntfy.sh/{NTFY}"
        print(f"Subscribe to: {self.target}")

    def update(self, weather):
        warning = weather.warning
        message = weather.message
        status = weather.is_good
        status_emoji = "\N{large green circle}" if status == True else "\N{large red circle}"

        requests.post(
                self.target,
                data = message.encode('utf-8'),
                headers = {f'Title':": ".join([status_emoji, warning]).encode('utf-8')}
                )

    def post(self, *a, **kwa):
        requests.post(
                self.target,
                *a, **kwa
                )

def main(args: List[str]):
    loop = Loop()
    weather = Weather()
    notifications = Notifications()
    notifications.post(
            data=(
                f'Started script at {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n'
                f'Report checks at: {loop.report_checks_string()}\n'
                f'Regular checks at: {loop.next_hour_checks_string()}'
                )
            )
    has_broken = False
    sleep = 60

    while True:
        assert sleep >= 60, f"if sleep is less than 60, it will break the script logic and send notifications few times in a minute; you have {sleep}"
        try:
            if loop.report_is_triggered():
                weather.check_report()
                notifications.update(weather) 
            if loop.regular_is_triggered():
                weather.check_next_hour()
                if not weather.is_good:
                    notifications.update(weather) 
            sleep = 60
            has_broken = False
        except Exception as e:
            if not has_broken:
                has_broken = True
                notifications.post(
                        data = (
                            f'Stopped working at: {datetime.now().strftime("%H:%M")}\n'
                            f'reason is: {e}\n'
                            'will now check every 15 minutes but push priority will be minimal'
                            ),
                        headers = {f'Title': 'weather stopped working', 'Priority':'max'}
                    )
                sleep = 60*15
            else:
                notifications.post(
                        data = f'still not working at: {datetime.now().strftime("%H:%M")}',
                        headers = {f'Title': 'weather not working', 'Priority':'min'}
                    )
                sleep = 3600

        # weather.check_next_hour()
        # weather.check_report()
        # notifications.update(weather)
    
        time.sleep(sleep)

if __name__ == '__main__':
    main(sys.argv[1:])

