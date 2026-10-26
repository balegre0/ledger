"""do-ecf-v1's receiver (spec §3.8, §10; Task 22): DGII's and emitters' routes to a company, in the receiver.

``routes`` mounts them under ``/do-ecf-v1/{environment}/{rnc}``; ``seeds`` and ``tokens`` keep the
receiver's own credentials (outside row-level security, hashed); ``uploads`` reads the one file a request
carries; ``answers`` writes what each route answers.
"""
