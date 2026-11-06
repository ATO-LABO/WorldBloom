"""Author initial stamina and shared costs without touching run history."""
import math
from execution.provenance import ConfigError


def number(value, name, minimum, maximum):
    if type(value) not in (int,float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigError(name,'設定値の範囲を確認してください',code='bad_request')
    return value


def settings(values):
    result={}
    for key,lo,hi in [('default_max',.01,100000),('default_recover_per_slot',0,100000),('exhausted_ratio',0,1),('default_action_cost',0,100000)]:
        result[key]=number(values[key],key,lo,hi)
    if type(values['passive_recovery']) is not bool:
        raise ConfigError('passive_recovery','時間経過での回復を指定してください',code='bad_request')
    result['passive_recovery']=values['passive_recovery']
    return result


def person(values):
    maximum=number(values['max'],'max',.01,100000)
    return dict(max=maximum,current=number(values['current'],'current',0,maximum),recover_per_slot=number(values['recover_per_slot'],'recover_per_slot',0,100000))
