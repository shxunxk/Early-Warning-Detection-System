Early Warning System (EWS)
Category: AI for Credit Risk Intelligence, Portfolio Monitoring & Proactive Risk Management
Applicable Domains: Digital Lending, Loan Management, Core Banking, SME Finance and
Financial Services
Focus: Early Risk Identification, Repayment Behaviour, Delinquency Prediction, Portfolio
Intelligence, Customer Monitoring and Preventive Interventions
Problem Statement
Financial institutions continuously manage the risk of customers failing to meet their
financial obligations. While traditional credit risk management focuses on assessing
customers before approving a loan, the customer's financial circumstances and repayment
behaviour may change significantly after disbursement.
A customer who was considered financially stable at the time of loan approval may
subsequently experience declining income, irregular cash flow, increasing debt obligations,
missed repayments or financial distress. These changes may occur gradually and may not
immediately result in a loan becoming delinquent. Traditional monitoring systems often rely
on predefined indicators such as Days Past Due (DPD), missed instalments or overdue
balances. While these indicators are important, they may identify risk only after repayment
difficulties have already started.
Similarly, a small business may experience declining sales receipts, increasing payment
obligations and irregular cash flows before it misses a loan instalment. Financial institutions
need to identify such warning signs early, understand their significance and initiate
appropriate preventive measures before the situation deteriorates.
The challenge is to build an AI-powered Early Warning System that continuously evaluates
customer repayment behaviour, financial exposure and emerging risk signals to identify
potential credit deterioration and recommend timely interventions. The application should
go beyond simply flagging overdue loans. It must analyse multiple indicators, recognise
changes in customer behaviour, distinguish temporary irregularities from sustained financial
deterioration and provide an explainable assessment of emerging credit risk.
The system should support both individual customer monitoring and portfolio-level risk
analysis. It should help credit risk teams, relationship managers and collections teams
understand which customers require attention, why their risk is increasing and what actions
may be appropriate.
Early Warning Systems cover a broad range of risks. Participants are encouraged to introduce
additional risk indicators, predictive techniques and monitoring capabilities beyond the
mandatory scenarios. Solutions demonstrating meaningful innovation in identifying
emerging financial stress or portfolio deterioration will receive additional consideration
within the evaluation framework.
The organiser will assess actual application behaviour rather than a static dashboard,
predefined presentation or standalone AI-generated response.
17
Early Warning Scenarios to Be Addressed
The solution should consider the following risk scenarios. These represent practical
situations in which financial institutions may benefit from identifying financial deterioration
before significant delinquency or default occurs.
1. Repayment Behaviour Deterioration
A customer who previously made regular repayments begins paying instalments late, making
partial payments or missing scheduled obligations. The system should identify changes in
repayment consistency, increasing Days Past Due, repeated missed instalments and other
repayment irregularities. It should distinguish an isolated payment delay from a recurring
pattern of deterioration.
2. Declining Income and Cash Flow
A customer or business experiences a gradual decline in income or account inflows. For
example, an SME customer whose average monthly business receipts decline over three
consecutive months may be experiencing financial pressure even though loan repayments
remain current. The system should identify sustained deterioration in cash flow, income
consistency and available financial capacity.
3. Increasing Financial Obligations and Exposure
A customer takes additional credit facilities, increases utilisation of existing limits or
accumulates higher repayment obligations. The application should assess whether the
customer's overall financial exposure is increasing faster than their ability to repay. It should
consider outstanding balances, existing obligations, available limits and exposure trends.
4. Temporary Missed Payments and Legitimate Exceptions
A customer misses one repayment due to a bank holiday, processing delay, temporary salary
delay or another isolated event. The system should recognise that not every missed payment
indicates significant financial deterioration. It should consider historical repayment
behaviour, subsequent payment recovery and the availability of supporting evidence before
assigning a warning level.
5. Early Delinquency and Roll-Forward Risk
A customer moves from regular repayment to early delinquency, such as 1–30 DPD, and
shows signs of progressing towards more serious overdue categories. The system should
identify the risk of continued deterioration based on repayment history, exposure and other
available signals. It should distinguish customers who recover quickly from those showing
repeated or worsening delinquency.
6. SME and Business Financial Stress
A business customer may experience declining sales receipts, irregular account activity,
reduced operating balances or increasing debt obligations. The application should identify
potential financial stress using available transaction, repayment and exposure data.
18
Participants may introduce additional SME indicators where suitable synthetic data is
available.
7. Missing or Incomplete Customer History
Some customers may have limited repayment records, missing transaction history or
insufficient financial information. The application must distinguish missing evidence from an
actual deterioration signal. It should not automatically classify a customer as high risk solely
because information is unavailable.
8. Portfolio Concentration and Emerging Risk Clusters
Several customers within the same product, industry, region or portfolio segment may begin
showing similar signs of financial deterioration. The system should identify concentrations
of emerging risk and allow users to investigate whether the pattern is isolated or affects a
broader customer segment.
19
Level 1: Identify, Analyse and Flag Early Credit Risk Signals
Suggested time: 0 - 3 hours
Objective: Build a working Early Warning System that ingests customer repayment and
exposure information, identifies emerging risk indicators, calculates warning scores and
presents customers requiring attention.
Required Capabilities
1. Customer and Credit Risk Data Ingestion
Build an application that allows users to upload or select customer and credit portfolio
information. The application should support the following sample data:
• Customer profiles containing customer identifiers, customer type, employment or
business category and portfolio classification.
• Loan and facility information containing product type, sanctioned amount,
outstanding balance, tenure, instalment amount and repayment schedule.
• Repayment histories containing due dates, payment dates, amounts due, amounts
paid and repayment status.
• Delinquency information containing Days Past Due, overdue balances and historical
delinquency flags.
• Customer exposure information containing existing facilities, outstanding
obligations, credit limits and utilisation.
• Financial or transaction indicators containing income, account inflows, outflows,
average balances and cash-flow trends.
• Risk signal definitions containing thresholds, conditions, weights and warning
classifications.
The application should accept CSV, Excel and JSON formats. It must validate uploaded
records, identify missing or inconsistent information and establish relationships between
customers, loan facilities, repayment histories and financial indicators.
2. Early Warning Signal Detection
Implement a mechanism that identifies emerging credit risk signals using configurable rules,
statistical analysis, machine learning or a combination of approaches. At a minimum, the
application must detect the following scenarios:
Scenario Expected detection behaviour
Repeated repayment
delays
Identify customers with recurring late or missed instalments
Temporary missed
payments
Distinguish isolated delays from sustained repayment
deterioration
Declining cash flow Identify customers showing sustained reductions in income or
account inflows
Increasing exposure Detect rising financial obligations or credit utilisation
Missing customer history Recognise insufficient information without inventing risk
indicators
20
The system should consider changes over time rather than evaluating every data point
independently. For example, a single missed instalment followed by immediate recovery
should be assessed differently from repeated late payments over several months.
3. Customer Warning Score Calculation
Develop a mechanism that calculates an Early Warning Score for each customer. The score
may use a numerical scale, such as 0–100, or categories such as Low, Medium, High and
Critical. The scoring mechanism should consider relevant indicators, including repayment
consistency, delinquency history, financial exposure, income or cash-flow deterioration and
the number of active warning signals.
Participants must clearly define whether a higher score represents greater risk. The
application must explain how the score was calculated and which factors contributed to the
result.
4. Customer Risk Watchlist
Develop a watchlist displaying customers who require monitoring or intervention. The
watchlist should show customer identifier, product or facility, outstanding exposure,
warning score, risk category, contributing indicators and monitoring status. Users should be
able to filter the watchlist by product, customer segment, risk level, delinquency status and
portfolio. Selecting a customer should display the underlying repayment history, exposure
information and warning indicators.
5. Basic Warning Review and Classification
Allow a risk analyst to inspect an early warning alert and record an initial decision. Supported
decisions should include:
• Continue Monitoring
• Requires Investigation
• False Positive
• Escalate for Review
The analyst should be able to provide a reason for the decision. The application must
preserve the original warning score and evidence even when the analyst changes the
classification.
Expected Submission
A working application demonstrating data ingestion, early warning signal detection,
customer risk scoring, an explainable watchlist and basic analyst review.