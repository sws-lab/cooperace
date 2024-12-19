// This file is part of the SV-Benchmarks collection of verification tasks:
// https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks
//
// SPDX-FileCopyrightText: 2021 F. Schuessele <schuessf@informatik.uni-freiburg.de>
// SPDX-FileCopyrightText: 2021 D. Klumpp <klumpp@informatik.uni-freiburg.de>
//
// SPDX-License-Identifier: LicenseRef-BSD-3-Clause-Attribution-Vandikas

typedef unsigned long int pthread_t;

union pthread_attr_t
{
  char __size[36];
  long int __align;
};
typedef union pthread_attr_t pthread_attr_t;

extern void __assert_fail(const char *__assertion, const char *__file,
      unsigned int __line, const char *__function)
     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
void reach_error() { __assert_fail("0", "parallel-ticket-7.wvr.c", 21, __extension__ __PRETTY_FUNCTION__); }
extern int pthread_create (pthread_t *__restrict __newthread,
      const pthread_attr_t *__restrict __attr,
      void *(*__start_routine) (void *),
      void *__restrict __arg) __attribute__ ((__nothrow__)) __attribute__ ((__nonnull__ (1, 3)));
extern int pthread_join (pthread_t __th, void **__thread_return);

extern int   __VERIFIER_nondet_int(void);
extern _Bool __VERIFIER_nondet_bool(void);

extern void abort(void);
void assume_abort_if_not(int cond) {
  if(!cond) {abort();}
}

int m1, m2, m3, m4, m5, m6, m7;
_Atomic int s, t, x;
_Atomic _Bool b;

void* thread1(void* _argptr) {
  m1 = t++;

  assume_abort_if_not( m1 <= s );
  x = 1;
  x = 0;
  s++;

  return 0;
}

void* thread2(void* _argptr) {
  m2 = t++;

  assume_abort_if_not( m2 <= s );
  if (x == 1) b = 1;
  s++;

  return 0;
}

void* thread3(void* _argptr) {
  m3 = t++;

  assume_abort_if_not( m3 <= s );
  if (x == 1) b = 1;
  s++;

  return 0;
}

void* thread4(void* _argptr) {
  m4 = t++;

  assume_abort_if_not( m4 <= s );
  if (x == 1) b = 1;
  s++;

  return 0;
}

void* thread5(void* _argptr) {
  m5 = t++;

  assume_abort_if_not( m5 <= s );
  if (x == 1) b = 1;
  s++;

  return 0;
}

void* thread6(void* _argptr) {
  m6 = t++;

  assume_abort_if_not( m6 <= s );
  if (x == 1) b = 1;
  s++;

  return 0;
}

void* thread7(void* _argptr) {
  m7 = t++;

  assume_abort_if_not( m7 <= s );
  if (x == 1) b = 1;
  s++;

  return 0;
}

int main() {
  pthread_t t1, t2, t3, t4, t5, t6, t7;

  // initialize global variables
  m1 = __VERIFIER_nondet_int();
  m2 = __VERIFIER_nondet_int();
  m3 = __VERIFIER_nondet_int();
  m4 = __VERIFIER_nondet_int();
  m5 = __VERIFIER_nondet_int();
  m6 = __VERIFIER_nondet_int();
  m7 = __VERIFIER_nondet_int();
  s  = __VERIFIER_nondet_int();
  t  = __VERIFIER_nondet_int();
  x  = __VERIFIER_nondet_int();
  b  = __VERIFIER_nondet_bool();

  // main method
  assume_abort_if_not( s == t && s == x && s == 0 && b == 0 );

  pthread_create(&t1, 0, thread1, 0);
  pthread_create(&t2, 0, thread2, 0);
  pthread_create(&t3, 0, thread3, 0);
  pthread_create(&t4, 0, thread4, 0);
  pthread_create(&t5, 0, thread5, 0);
  pthread_create(&t6, 0, thread6, 0);
  pthread_create(&t7, 0, thread7, 0);
  pthread_join(t1, 0);
  pthread_join(t2, 0);
  pthread_join(t3, 0);
  pthread_join(t4, 0);
  pthread_join(t5, 0);
  pthread_join(t6, 0);
  pthread_join(t7, 0);

  assume_abort_if_not(b);
  reach_error();

  return 0;
}