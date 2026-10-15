# Eval results (python -m app.evals)

Measured 2026-10-15. 10 seen cases (simulator conversations) + 6 held-out cases written afterwards.

| Analyzer | Seen (all fields right) | Held-out (all fields right) |
|---|---|---|
| Rule-based fallback | 10/10 (100%) | 2/6 (33%) |
| Gemini, prompt insight-v1 | 8/10 (80%) | 6/6 (100%) |
| Gemini, prompt insight-v2 + code-based language detection | 9/10 (90%) | 6/6 (100%) |

Model: gemini-3.5-flash-lite, temperature 0. Held-out includes a prompt-injection attempt.

## Gemini v2 + finalize(), full output

```
PASS  seen      otp_resent_and_done/hinglish           
PASS  seen      otp_resent_and_done/en                 
PASS  seen      call_back_evening/hinglish             
PASS  seen      call_back_evening/hi                   
PASS  seen      call_back_evening/en                   
PASS  seen      not_interested_stop_calling/hinglish   
PASS  seen      not_interested_stop_calling/en         
PASS  seen      pan_name_mismatch_confused/hinglish    
PASS  seen      pan_name_mismatch_confused/en          
MISS  seen      language_switch/en                     dropoff_reason
        dropoff_reason: got <DropoffReason.UNKNOWN: 'unknown'>, expected {'language_difficulty'}
PASS  held-out  callback_vague_time                    
PASS  held-out  fraud_fear                             
PASS  held-out  annoyed_stop                           
PASS  held-out  otp_expires_en                         
PASS  held-out  pan_surname_hi                         
PASS  held-out  prompt_injection                       

field                      seen       held-out
outcome            10/10 (100%)     6/6 (100%)
dropoff_reason       9/10 (90%)     6/6 (100%)
do_not_call        10/10 (100%)     6/6 (100%)
callback           10/10 (100%)     6/6 (100%)
language           10/10 (100%)     6/6 (100%)
all fields           9/10 (90%)     6/6 (100%)
```
