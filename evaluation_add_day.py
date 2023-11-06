"""
Lily Li
Created 2022-10

Add calendar days to the evaluation output
"""

import pandas as pd
import pdb
import pickle

if __name__ == "__main__":
    df_left = pd.read_pickle("~/../../mnt/c/Users/Public/2_Analysis_Data/Microsoft/2022_01_29/excludelast3days_calendardays/FinalTestDataOneInstancePerDay2/claims_visits_ed_3.pkl")
    df_right = pd.read_csv("./10match_control_072022/excludelast3days/Results_model_ed_19_072622/evaluation_results_perday.csv")
    df_res = df_left.merge(df_right, left_index=True, right_index = True)
    lst = ['PID','visit_num','day', 'Actual', 'Predicted_Rounded', 'PredictedNotRounded', 'target_y']
    res = df_res[lst]

    res.rename(columns={'target_y':'target'}, inplace=True)
    res.sort_values(by=['PID', 'visit_num'], inplace=True)
    res.to_csv("./10match_control_072022/excludelast3days/Results_model_ed_19_072622/evaluation_results_perday_withdays.csv")
    
    
