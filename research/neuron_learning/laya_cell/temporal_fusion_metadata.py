"""Pure metadata shared by temporal fusion installation, training and evaluation."""

PROFILE={
 "schema":"heptatrader.temporal-laya-fusion-profile.v1","instrument":"EUR.USD","horizon_sec":300,
 "feature_version":"causal-fixed-scale-features16-v1","feature_width":16,"max_sequence":24,
 "embedding_dim":32,"classes":7,"laya_projection_seed":2026100802,"temporal_hidden":32,
 "temporal_layers":1,"dropout":0.0,"objective":"FUSION_CE_PLUS_0.25_NUMERIC_CE",
 "optimizer":"AdamW","learning_rate":0.001,"weight_decay":0.0001,"epochs":48,
 "batch_size":32,"gradient_norm_cap":1.0,"max_training_rows":192,"internal_holdout_rows":32,
 "automatic_trading":False,"actor_update":False,"financial_reward_used":False,
}

RULE={"schema":"heptatrader.temporal-fusion-shadow-rule.v1","slots":12,"slot_spacing_ms":360000,
 "horizon_ms":300000,"input_grace_ms":60000,"minimum_observed":10,"maximum_censored":2,
 "minimum_target_classes":2,"minimum_mean_brier_improvement":1e-5,
 "one_sided_sign_alpha_each_baseline":0.025,"automatic_selection":False,"trade_permission":False}
